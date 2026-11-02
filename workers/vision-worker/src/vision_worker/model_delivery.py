from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin
from uuid import uuid4

import httpx
from fr_contracts.runtime_status import PersonDetectorRuntimeStatus
from fr_domain.model_management import DeploymentBackend, ModelManifest, ModelType

from vision_worker.inference.adaface_onnx import AdaFaceContractError, AdaFaceOnnxEmbedder
from vision_worker.inference.interfaces import BoundingBox, FaceDetection, ModelStageTimings
from vision_worker.inference.onnx_session import OnnxInferenceSession, OnnxSessionError
from vision_worker.inference.scrfd_onnx import ScrfdContractError, ScrfdOnnxDetector
from vision_worker.inference.yolo_onnx import YoloContractError, YoloOnnxDetector
from vision_worker.model_runtime import (
    AtomicModelSet,
    InferenceRuntimeResolver,
    ModelArtifacts,
    PreparedModelRuntime,
    ResolvedModelRuntime,
    RuntimeCapabilities,
    RuntimeResolutionError,
)
from vision_worker.video.frame import VideoFrame


class ModelDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PendingModelDeployment:
    deployment_id: str
    model_type: ModelType
    requested_backend: DeploymentBackend
    artifact_path: Path
    artifact_checksum: str
    version: str
    precision: str
    manifest: ModelManifest | None = None
    revision: str = ""
    model_definition_id: str = ""
    model_version_id: str = ""
    delivery_error: str | None = None

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        artifact_path: Path | None = None,
        delivery_error: str | None = None,
    ) -> PendingModelDeployment:
        deployment = payload["deployment"]
        definition = payload["definition"]
        version = payload["version"]
        artifact = payload["artifact"]
        return cls(
            deployment_id=str(deployment["id"]),
            model_type=ModelType(definition["model_type"]),
            requested_backend=DeploymentBackend(deployment["backend"]),
            artifact_path=artifact_path or Path(str(artifact.get("storage_key", ""))),
            artifact_checksum=str(artifact["checksum"]),
            version=str(version["version"]),
            precision=str(deployment["precision"]),
            manifest=ModelManifest.model_validate(version["manifest"]),
            revision=str(deployment["revision"]),
            model_definition_id=str(definition["id"]),
            model_version_id=str(version["id"]),
            delivery_error=delivery_error,
        )


class DeploymentProvider(Protocol):
    def pending(self) -> list[PendingModelDeployment]: ...

    def report(
        self,
        deployment_id: str,
        *,
        success: bool,
        resolved: ResolvedModelRuntime | None = None,
        error_category: str | None = None,
    ) -> None: ...


class ModelAdapterFactory(Protocol):
    def prepare(
        self,
        deployment: PendingModelDeployment,
        resolved: ResolvedModelRuntime,
    ) -> PreparedModelRuntime: ...


class UnavailableProductionAdapterFactory:
    """Truthful boundary until real YOLO/SCRFD/AdaFace adapters are installed."""

    def prepare(
        self,
        deployment: PendingModelDeployment,
        resolved: ResolvedModelRuntime,
    ) -> PreparedModelRuntime:
        del deployment, resolved
        raise ModelDeliveryError("REAL_MODEL_ADAPTER_NOT_IMPLEMENTED")


class PreparedYoloDetector:
    adapter_kind = "yolo_onnx"
    model_type = ModelType.PERSON_DETECTOR

    def __init__(
        self, deployment_id: str, version: str, checksum: str, detector: YoloOnnxDetector
    ) -> None:
        self.deployment_id = deployment_id
        self._version = version
        self._checksum_prefix = checksum[:12]
        self._loaded_at = datetime.now(UTC).isoformat()
        self._detector = detector

    def detect(self, frames: Sequence[VideoFrame]) -> list[list[BoundingBox]]:
        return self._detector.detect(frames)

    def warmup(self) -> None:
        self._detector.warmup()

    def health_check(self) -> None:
        if not self._detector.warmed_up:
            raise ModelDeliveryError("MODEL_WARMUP_FAILED")

    def close(self) -> None:
        self._detector.close()

    def runtime_status(self) -> PersonDetectorRuntimeStatus:
        return PersonDetectorRuntimeStatus(
            deployment_id=self.deployment_id,
            model_version=self._version,
            checksum_prefix=self._checksum_prefix,
            execution_provider=self._detector.execution_provider,
            loaded_at=self._loaded_at,
            inference_count=self._detector.inference_count,
            inference_failures=self._detector.inference_failures,
            latest_latency_ms=self._detector.last_total_ms,
            mean_latency_ms=(
                self._detector.total_latency_ms
                / (self._detector.inference_count + self._detector.inference_failures)
                if self._detector.inference_count + self._detector.inference_failures
                else None
            ),
            health=(
                "ACTIVE"
                if self._detector.warmed_up and not self._detector.last_attempt_failed
                else "ERROR"
            ),
        )


class PreparedScrfdDetector:
    adapter_kind = "scrfd_onnx"
    model_type = ModelType.FACE_DETECTOR

    def __init__(self, deployment: PendingModelDeployment, detector: ScrfdOnnxDetector) -> None:
        self.deployment_id = deployment.deployment_id
        self.model_version_id = deployment.model_version_id
        self._detector = detector

    def detect(self, person_crops: Sequence[Any]) -> list[list[FaceDetection]]:
        return self._detector.detect(person_crops)

    def timings(self) -> ModelStageTimings:
        return self._detector.timings()

    def warmup(self) -> None:
        self._detector.warmup()

    def health_check(self) -> None:
        if not self._detector.warmed_up:
            raise ModelDeliveryError("MODEL_WARMUP_FAILED")

    def close(self) -> None:
        self._detector.close()

    @property
    def execution_provider(self) -> str:
        return self._detector.execution_provider


class PreparedAdaFaceEmbedder:
    adapter_kind = "adaface_onnx"
    model_type = ModelType.FACE_EMBEDDER

    def __init__(self, deployment: PendingModelDeployment, embedder: AdaFaceOnnxEmbedder) -> None:
        self.deployment_id = deployment.deployment_id
        self.model_definition_id = deployment.model_definition_id
        self.model_version_id = deployment.model_version_id
        self.model_version = deployment.version
        self.embedding_dimension = embedder.embedding_dimension
        self._embedder = embedder

    def embed(self, aligned_faces: Sequence[Any]) -> list[list[float]]:
        return self._embedder.embed(aligned_faces)

    def timings(self) -> ModelStageTimings:
        return self._embedder.timings()

    def warmup(self) -> None:
        self._embedder.warmup()

    def health_check(self) -> None:
        if not self._embedder.warmed_up:
            raise ModelDeliveryError("MODEL_WARMUP_FAILED")

    def close(self) -> None:
        self._embedder.close()

    @property
    def execution_provider(self) -> str:
        return self._embedder.execution_provider


class ProductionAdapterFactory:
    """Only declared YOLO/YOLOX, SCRFD and AdaFace ONNX CPU profiles are available."""

    supported_backends = frozenset({DeploymentBackend.ONNX_CPU})

    def prepare(
        self,
        deployment: PendingModelDeployment,
        resolved: ResolvedModelRuntime,
    ) -> PreparedModelRuntime:
        if resolved.backend is not DeploymentBackend.ONNX_CPU:
            raise ModelDeliveryError("ADAPTER_UNAVAILABLE")
        if deployment.precision != "FP32":
            raise ModelDeliveryError("UNSUPPORTED_PRECISION")
        if deployment.manifest is None:
            raise ModelDeliveryError("INVALID_TENSOR_CONTRACT")
        if deployment.manifest.model_type is not deployment.model_type:
            raise ModelDeliveryError("MODEL_TYPE_MISMATCH")
        if DeploymentBackend.ONNX_CPU not in deployment.manifest.runtime_compatibility:
            raise ModelDeliveryError("RUNTIME_INCOMPATIBLE")
        session = OnnxInferenceSession(
            deployment.artifact_path,
            deployment.artifact_checksum,
            deployment.manifest,
            resolved.execution_provider,
        )
        adapter: YoloOnnxDetector | ScrfdOnnxDetector | AdaFaceOnnxEmbedder
        try:
            if deployment.model_type is ModelType.PERSON_DETECTOR:
                adapter = YoloOnnxDetector(session, deployment.manifest)
            elif deployment.model_type is ModelType.FACE_DETECTOR:
                adapter = ScrfdOnnxDetector(session, deployment.manifest)
            elif deployment.model_type is ModelType.FACE_EMBEDDER:
                adapter = AdaFaceOnnxEmbedder(session, deployment.manifest)
            else:
                raise ModelDeliveryError("REAL_MODEL_ADAPTER_NOT_IMPLEMENTED")
        except Exception:
            session.close()
            raise
        if isinstance(adapter, YoloOnnxDetector):
            return PreparedYoloDetector(
                deployment.deployment_id, deployment.version, deployment.artifact_checksum, adapter
            )
        if isinstance(adapter, ScrfdOnnxDetector):
            return PreparedScrfdDetector(deployment, adapter)
        return PreparedAdaFaceEmbedder(deployment, adapter)


class HttpDeploymentProvider:
    def __init__(
        self,
        base_url: str,
        worker_token: str,
        timeout_seconds: float = 10.0,
        artifact_cache_dir: Path | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Worker-Token": worker_token}
        self._timeout = timeout_seconds
        self._artifact_cache_dir = artifact_cache_dir or Path(
            os.getenv("FR_MODEL_CACHE_ROOT", "data/model-cache")
        )

    @staticmethod
    def _file_checksum(path: Path) -> tuple[str, int]:
        digest = sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _download_artifact(self, artifact: dict[str, Any]) -> Path:
        checksum = str(artifact.get("checksum", ""))
        size_bytes = int(artifact.get("size_bytes", -1))
        content_url = str(artifact.get("content_url", ""))
        if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
            raise ModelDeliveryError("INVALID_ARTIFACT_METADATA")
        if size_bytes < 0 or not content_url.startswith("/api/v1/internal/model-artifacts/"):
            raise ModelDeliveryError("INVALID_ARTIFACT_METADATA")
        self._artifact_cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._artifact_cache_dir / f"{checksum}.onnx"
        if target.is_file():
            try:
                existing_checksum, existing_size = self._file_checksum(target)
                if existing_checksum == checksum and existing_size == size_bytes:
                    return target
            except OSError:
                pass

        temporary = self._artifact_cache_dir / f".{checksum}.{uuid4().hex}.part"
        digest = sha256()
        written = 0
        try:
            url = urljoin(self._base_url + "/", content_url.lstrip("/"))
            with httpx.stream("GET", url, headers=self._headers, timeout=self._timeout) as response:
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > size_bytes:
                            raise ModelDeliveryError("ARTIFACT_SIZE_MISMATCH")
                        digest.update(chunk)
                        output.write(chunk)
            if written != size_bytes:
                raise ModelDeliveryError("ARTIFACT_SIZE_MISMATCH")
            if digest.hexdigest() != checksum:
                raise ModelDeliveryError("CHECKSUM_MISMATCH")
            os.replace(temporary, target)
            return target
        except ModelDeliveryError:
            raise
        except httpx.HTTPStatusError as error:
            raise ModelDeliveryError("ARTIFACT_DOWNLOAD_FAILED") from error
        except OSError as error:
            raise ModelDeliveryError("ARTIFACT_CACHE_FAILED") from error
        except httpx.HTTPError as error:
            raise ModelDeliveryError("ARTIFACT_DOWNLOAD_FAILED") from error
        finally:
            temporary.unlink(missing_ok=True)

    def _deployments(self, items: list[dict[str, Any]]) -> list[PendingModelDeployment]:
        deployments = []
        for item in items:
            artifact = item.get("artifact", {})
            if not artifact.get("content_url"):
                deployments.append(PendingModelDeployment.from_api(item))
                continue
            try:
                local_path = self._download_artifact(artifact)
                deployments.append(PendingModelDeployment.from_api(item, artifact_path=local_path))
            except ModelDeliveryError as error:
                deployments.append(
                    PendingModelDeployment.from_api(
                        item, artifact_path=Path(), delivery_error=str(error)
                    )
                )
        return deployments

    def pending(self) -> list[PendingModelDeployment]:
        response = httpx.get(
            f"{self._base_url}/api/v1/internal/model-deployments/pending",
            headers=self._headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return self._deployments(response.json()["items"])

    def assigned(self) -> list[PendingModelDeployment]:
        response = httpx.get(
            f"{self._base_url}/api/v1/internal/model-deployments/assigned",
            headers=self._headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return self._deployments(response.json()["items"])

    def report(
        self,
        deployment_id: str,
        *,
        success: bool,
        resolved: ResolvedModelRuntime | None = None,
        error_category: str | None = None,
    ) -> None:
        body: dict[str, object] = {"success": success, "error_category": error_category}
        if resolved is not None:
            body.update(
                resolved_backend=resolved.backend.value,
                execution_provider=resolved.execution_provider,
                fallback_reason=resolved.fallback_reason,
            )
        response = httpx.post(
            f"{self._base_url}/api/v1/internal/model-deployments/{deployment_id}/activation-result",
            headers=self._headers,
            json=body,
            timeout=self._timeout,
        )
        response.raise_for_status()


class ModelDeploymentSynchronizer:
    def __init__(
        self,
        provider: DeploymentProvider,
        capabilities: RuntimeCapabilities,
        adapter_factory: ModelAdapterFactory,
        active_models: AtomicModelSet | None = None,
        resolver: InferenceRuntimeResolver | None = None,
    ) -> None:
        self._provider = provider
        self._capabilities = capabilities
        self._factory = adapter_factory
        self.active_models = active_models or AtomicModelSet()
        self._resolver = resolver or InferenceRuntimeResolver()
        self._loaded_revisions: dict[str, str] = {}
        self._assigned_ids: set[str] = set()

    def active_person_detector(self) -> PreparedYoloDetector | None:
        runtime = self.active_models.active(ModelType.PERSON_DETECTOR)
        if (
            isinstance(runtime, PreparedYoloDetector)
            and runtime.deployment_id in self._assigned_ids
        ):
            return runtime
        return None

    def active_face_detector(self) -> PreparedScrfdDetector | None:
        runtime = self.active_models.active(ModelType.FACE_DETECTOR)
        if (
            isinstance(runtime, PreparedScrfdDetector)
            and runtime.deployment_id in self._assigned_ids
        ):
            return runtime
        return None

    def active_face_embedder(self) -> PreparedAdaFaceEmbedder | None:
        runtime = self.active_models.active(ModelType.FACE_EMBEDDER)
        if (
            isinstance(runtime, PreparedAdaFaceEmbedder)
            and runtime.deployment_id in self._assigned_ids
        ):
            return runtime
        return None

    @staticmethod
    def _verify_artifact(deployment: PendingModelDeployment) -> None:
        try:
            payload = deployment.artifact_path.read_bytes()
        except OSError as error:
            raise ModelDeliveryError("ARTIFACT_MISSING") from error
        if sha256(payload).hexdigest() != deployment.artifact_checksum:
            raise ModelDeliveryError("CHECKSUM_MISMATCH")

    def sync_once(self) -> list[str]:
        processed: list[str] = []
        deployments: list[PendingModelDeployment] = []
        previous_assigned_ids = set(self._assigned_ids)
        assigned_roles: set[ModelType] = set()
        if isinstance(self._provider, HttpDeploymentProvider):
            assigned = self._provider.assigned()
            self._assigned_ids = {item.deployment_id for item in assigned}
            assigned_roles = {item.model_type for item in assigned}
            deployments.extend(assigned)
        deployments.extend(self._provider.pending())
        for deployment in deployments:
            active = self.active_models.active(deployment.model_type)
            if (
                self._loaded_revisions.get(deployment.deployment_id) == deployment.revision
                and active is not None
                and active.deployment_id == deployment.deployment_id
            ):
                continue
            resolved: ResolvedModelRuntime | None = None
            try:
                if deployment.delivery_error:
                    raise ModelDeliveryError(deployment.delivery_error)
                self._verify_artifact(deployment)
                supported = getattr(self._factory, "supported_backends", None)
                resolved = self._resolver.resolve(
                    deployment.requested_backend,
                    ModelArtifacts(onnx=deployment.artifact_path.suffix.lower() == ".onnx"),
                    self._capabilities,
                    supported,
                )
                candidate = self._factory.prepare(deployment, resolved)
                if (
                    isinstance(self._provider, HttpDeploymentProvider)
                    and deployment.deployment_id not in self._assigned_ids
                ):
                    # Activation validates a candidate; assignment chooses production use.
                    # A pending unassigned model must never evict an assigned session.
                    try:
                        candidate.warmup()
                        candidate.health_check()
                    finally:
                        candidate.close()
                else:
                    self.active_models.activate(candidate)
            except (
                ModelDeliveryError,
                RuntimeResolutionError,
                OnnxSessionError,
                YoloContractError,
                ScrfdContractError,
                AdaFaceContractError,
            ) as error:
                if (
                    active is not None
                    and deployment.model_type in assigned_roles
                    and active.deployment_id in previous_assigned_ids
                ):
                    self._assigned_ids.add(active.deployment_id)
                self._provider.report(
                    deployment.deployment_id,
                    success=False,
                    error_category=str(error),
                )
            except Exception:
                if (
                    active is not None
                    and deployment.model_type in assigned_roles
                    and active.deployment_id in previous_assigned_ids
                ):
                    self._assigned_ids.add(active.deployment_id)
                self._provider.report(
                    deployment.deployment_id,
                    success=False,
                    error_category="MODEL_LOAD_FAILED",
                )
            else:
                self._loaded_revisions[deployment.deployment_id] = deployment.revision
                self._provider.report(
                    deployment.deployment_id,
                    success=True,
                    resolved=resolved,
                )
            processed.append(deployment.deployment_id)
        return processed


def discover_runtime_capabilities() -> RuntimeCapabilities:
    """Probe ONNX Runtime lazily; CPU-only startup never imports NVIDIA modules."""
    try:
        import onnxruntime as ort  # type: ignore[import-untyped]
    except ImportError:
        return RuntimeCapabilities(False, False, False)
    providers = tuple(ort.get_available_providers())
    return RuntimeCapabilities(
        onnx_cpu_available="CPUExecutionProvider" in providers,
        onnx_cuda_available="CUDAExecutionProvider" in providers,
        tensorrt_available="TensorrtExecutionProvider" in providers,
        execution_providers=providers,
    )
