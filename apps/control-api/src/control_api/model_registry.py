from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import uuid4

from fr_domain.model_management import (
    ArtifactFormat,
    DeploymentBackend,
    DeploymentStatus,
    ModelDefinition,
    ModelDeployment,
    ModelManifest,
    ModelRuntimeState,
    ModelType,
    ModelVersion,
    ValidationStatus,
)

from control_api.model_artifacts import ModelArtifact, ModelArtifactIngestor


@dataclass(frozen=True, slots=True)
class DeploymentRuntimeStatus:
    deployment_id: str
    state: ModelRuntimeState
    resolved_backend: DeploymentBackend | None = None
    execution_provider: str | None = None
    fallback_reason: str | None = None
    loaded_at: str | None = None
    last_successful_inference_at: str | None = None
    error_category: str | None = None


class ModelRepository(Protocol):
    def put_definition(self, definition: ModelDefinition) -> None: ...
    def get_definition(self, definition_id: str) -> ModelDefinition | None: ...
    def list_definitions(self) -> list[ModelDefinition]: ...
    def put_artifact(self, artifact: ModelArtifact) -> None: ...
    def get_artifact(self, artifact_id: str) -> ModelArtifact | None: ...
    def list_artifacts(self) -> list[ModelArtifact]: ...
    def put_version(self, version: ModelVersion) -> None: ...
    def put_artifact_and_version(self, artifact: ModelArtifact, version: ModelVersion) -> None: ...
    def get_version(self, version_id: str) -> ModelVersion | None: ...
    def list_versions(self, definition_id: str) -> list[ModelVersion]: ...
    def delete_version(self, version_id: str) -> None: ...
    def put_deployment(self, deployment: ModelDeployment) -> None: ...
    def get_deployment(self, deployment_id: str) -> ModelDeployment | None: ...
    def list_deployments(self) -> list[ModelDeployment]: ...
    def put_assignment(self, model_type: ModelType, deployment_id: str) -> None: ...
    def list_assignments(self) -> dict[ModelType, str]: ...


class InMemoryModelRepository:
    def __init__(self) -> None:
        self.definitions: dict[str, ModelDefinition] = {}
        self.artifacts: dict[str, ModelArtifact] = {}
        self.versions: dict[str, ModelVersion] = {}
        self.deployments: dict[str, ModelDeployment] = {}
        self.assignments: dict[ModelType, str] = {}
        self._lock = RLock()

    def put_definition(self, definition: ModelDefinition) -> None:
        self.definitions[definition.id] = definition

    def get_definition(self, definition_id: str) -> ModelDefinition | None:
        return self.definitions.get(definition_id)

    def list_definitions(self) -> list[ModelDefinition]:
        return sorted(self.definitions.values(), key=lambda item: (item.name, item.id))

    def put_artifact(self, artifact: ModelArtifact) -> None:
        self.artifacts[artifact.id] = artifact

    def get_artifact(self, artifact_id: str) -> ModelArtifact | None:
        return self.artifacts.get(artifact_id)

    def list_artifacts(self) -> list[ModelArtifact]:
        return list(self.artifacts.values())

    def put_version(self, version: ModelVersion) -> None:
        self.versions[version.id] = version

    def put_artifact_and_version(self, artifact: ModelArtifact, version: ModelVersion) -> None:
        with self._lock:
            if version.artifact_id != artifact.id:
                raise ValueError("ARTIFACT_VERSION_MISMATCH")
            if artifact.id in self.artifacts or version.id in self.versions:
                raise ValueError("MODEL_VERSION_EXISTS")
            self.artifacts[artifact.id] = artifact
            self.versions[version.id] = version

    def get_version(self, version_id: str) -> ModelVersion | None:
        return self.versions.get(version_id)

    def list_versions(self, definition_id: str) -> list[ModelVersion]:
        return sorted(
            (item for item in self.versions.values() if item.model_definition_id == definition_id),
            key=lambda item: (item.created_at, item.id),
        )

    def delete_version(self, version_id: str) -> None:
        with self._lock:
            related = [d for d in self.deployments.values() if d.model_version_id == version_id]
            if any(
                d.id in self.assignments.values()
                or d.status in {DeploymentStatus.ACTIVE, DeploymentStatus.ACTIVATION_PENDING}
                for d in related
            ):
                raise ValueError("ARTIFACT_IN_USE")
            for deployment in related:
                del self.deployments[deployment.id]
            del self.versions[version_id]

    def put_deployment(self, deployment: ModelDeployment) -> None:
        self.deployments[deployment.id] = deployment

    def get_deployment(self, deployment_id: str) -> ModelDeployment | None:
        return self.deployments.get(deployment_id)

    def list_deployments(self) -> list[ModelDeployment]:
        return list(self.deployments.values())

    def put_assignment(self, model_type: ModelType, deployment_id: str) -> None:
        self.assignments[model_type] = deployment_id

    def list_assignments(self) -> dict[ModelType, str]:
        return dict(self.assignments)


class ModelRegistryService:
    def __init__(
        self,
        repository: ModelRepository,
        ingestor: ModelArtifactIngestor,
    ) -> None:
        self.repository = repository
        self._ingestor = ingestor
        self._vector_index_status = "NOT_CONFIGURED"
        self._assignments = repository.list_assignments()
        self._runtime_statuses: dict[str, DeploymentRuntimeStatus] = {}

    @property
    def max_upload_bytes(self) -> int:
        return self._ingestor.max_size_bytes

    def create_definition(
        self,
        name: str,
        model_type: ModelType,
        framework: str,
        description: str = "",
    ) -> ModelDefinition:
        definition = ModelDefinition.create(name, model_type, framework, description)
        self.repository.put_definition(definition)
        return definition

    def get_definition(self, definition_id: str) -> ModelDefinition:
        definition = self.repository.get_definition(definition_id)
        if definition is None:
            raise KeyError(definition_id)
        return definition

    def list_definitions(self) -> list[ModelDefinition]:
        return self.repository.list_definitions()

    def create_version(
        self,
        definition_id: str,
        version_name: str,
        filename: str,
        payload: bytes,
        manifest: ModelManifest,
    ) -> ModelVersion:
        definition = self.get_definition(definition_id)
        if any(
            item.version == version_name for item in self.repository.list_versions(definition_id)
        ):
            raise ValueError("MODEL_VERSION_EXISTS")
        version_id = str(uuid4())
        artifact = self._ingestor.ingest(
            definition_id,
            version_id,
            filename,
            payload,
            manifest,
            expected_type=definition.model_type,
        )
        version = ModelVersion(
            version_id,
            definition_id,
            version_name,
            artifact.id,
            ArtifactFormat.ONNX,
            artifact.checksum,
            artifact.size_bytes,
            manifest,
            ValidationStatus.VALID,
            None,
            datetime.now(UTC).isoformat(),
        )
        try:
            self.repository.put_artifact_and_version(artifact, version)
        except Exception:
            try:
                self._ingestor.delete(artifact.storage_key)
            except Exception:
                # Metadata failed; preserve that error and allow later orphan cleanup.
                pass
            raise
        return version

    def list_versions(self, definition_id: str) -> list[ModelVersion]:
        self.get_definition(definition_id)
        return self.repository.list_versions(definition_id)

    def get_version(self, version_id: str) -> ModelVersion:
        version = self.repository.get_version(version_id)
        if version is None:
            raise KeyError(version_id)
        return version

    def create_deployment(
        self,
        version_id: str,
        backend: DeploymentBackend,
        precision: str,
    ) -> ModelDeployment:
        version = self.get_version(version_id)
        if version.validation_status is not ValidationStatus.VALID:
            raise ValueError("MODEL_VERSION_NOT_VALID")
        if precision not in {"FP32", "FP16"}:
            raise ValueError("UNSUPPORTED_PRECISION")
        if precision == "FP16" and backend is DeploymentBackend.ONNX_CPU:
            raise ValueError("UNSUPPORTED_PRECISION")
        deployment = ModelDeployment.create(version.id, backend, precision)
        self.repository.put_deployment(deployment)
        return deployment

    def get_deployment(self, deployment_id: str) -> ModelDeployment:
        deployment = self.repository.get_deployment(deployment_id)
        if deployment is None:
            raise KeyError(deployment_id)
        return deployment

    def request_activation(self, deployment_id: str) -> ModelDeployment:
        deployment = self.get_deployment(deployment_id)
        pending = replace(
            deployment,
            status=DeploymentStatus.ACTIVATION_PENDING,
            revision=str(uuid4()),
            error_category=None,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.repository.put_deployment(pending)
        self._runtime_statuses[deployment_id] = DeploymentRuntimeStatus(
            deployment_id, ModelRuntimeState.LOADING
        )
        return pending

    def record_activation(
        self,
        deployment_id: str,
        *,
        success: bool,
        resolved_backend: DeploymentBackend | None = None,
        error_category: str | None = None,
        execution_provider: str | None = None,
        fallback_reason: str | None = None,
    ) -> ModelDeployment:
        deployment = self.get_deployment(deployment_id)
        if not success:
            failed = replace(
                deployment,
                status=DeploymentStatus.ERROR,
                error_category=error_category or "MODEL_LOAD_FAILED",
                updated_at=datetime.now(UTC).isoformat(),
            )
            self.repository.put_deployment(failed)
            self._runtime_statuses[deployment_id] = DeploymentRuntimeStatus(
                deployment_id, ModelRuntimeState.ERROR, error_category=failed.error_category
            )
            return failed
        if resolved_backend is None:
            raise ValueError("RESOLVED_BACKEND_REQUIRED")
        version = self.get_version(deployment.model_version_id)
        definition = self.get_definition(version.model_definition_id)
        for current in self.repository.list_deployments():
            if current.id == deployment.id or current.status is not DeploymentStatus.ACTIVE:
                continue
            current_version = self.get_version(current.model_version_id)
            current_definition = self.get_definition(current_version.model_definition_id)
            if current_definition.model_type is definition.model_type:
                self.repository.put_deployment(
                    replace(
                        current,
                        status=DeploymentStatus.INACTIVE,
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                )
        active = replace(
            deployment,
            status=DeploymentStatus.ACTIVE,
            resolved_backend=resolved_backend,
            error_category=None,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.repository.put_deployment(active)
        self._runtime_statuses[deployment_id] = DeploymentRuntimeStatus(
            deployment_id,
            ModelRuntimeState.ACTIVE,
            resolved_backend,
            execution_provider,
            fallback_reason,
            datetime.now(UTC).isoformat(),
        )
        return active

    def delete_version(self, version_id: str) -> None:
        version = self.get_version(version_id)
        referenced = [
            item
            for item in self.repository.list_deployments()
            if item.model_version_id == version.id
            and (
                item.status in {DeploymentStatus.ACTIVE, DeploymentStatus.ACTIVATION_PENDING}
                or item.id in self.repository.list_assignments().values()
            )
        ]
        if referenced:
            raise ValueError("ARTIFACT_IN_USE")
        self.repository.delete_version(version_id)

    def update_person_version(
        self, version_id: str, label: str, confidence: float | None = None
    ) -> ModelVersion:
        version = self.get_version(version_id)
        if version.manifest.model_type is not ModelType.PERSON_DETECTOR:
            raise KeyError(version_id)
        label = label.strip()
        if not label or len(label) > 80:
            raise ValueError("INVALID_VERSION_LABEL")
        if any(
            v.id != version_id and v.version == label
            for v in self.repository.list_versions(version.model_definition_id)
        ):
            raise ValueError("MODEL_VERSION_EXISTS")
        manifest = version.manifest
        if confidence is not None and confidence != manifest.metadata.get(
            "confidence_threshold", 0.25
        ):
            if not 0.01 <= confidence <= 1:
                raise ValueError("INVALID_CONFIDENCE")
            if any(d.model_version_id == version_id for d in self.repository.list_deployments()):
                raise ValueError("MODEL_PROFILE_IMMUTABLE_AFTER_DEPLOYMENT")
            data = manifest.model_dump(mode="json")
            data["metadata"]["confidence_threshold"] = confidence
            manifest = ModelManifest.model_validate(data)
        updated = replace(version, version=label, manifest=manifest)
        self.repository.put_version(updated)
        return updated

    def list_deployments(self) -> list[ModelDeployment]:
        return self.repository.list_deployments()

    def pending_deployments(self) -> list[ModelDeployment]:
        return [
            item
            for item in self.repository.list_deployments()
            if item.status is DeploymentStatus.ACTIVATION_PENDING
        ]

    def deactivate(self, deployment_id: str) -> ModelDeployment:
        deployment = self.get_deployment(deployment_id)
        inactive = replace(
            deployment,
            status=DeploymentStatus.INACTIVE,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.repository.put_deployment(inactive)
        self._runtime_statuses[deployment_id] = DeploymentRuntimeStatus(
            deployment_id, ModelRuntimeState.NOT_LOADED
        )
        return inactive

    def runtime_status(self, deployment_id: str) -> DeploymentRuntimeStatus:
        self.get_deployment(deployment_id)
        return self._runtime_statuses.get(
            deployment_id,
            DeploymentRuntimeStatus(deployment_id, ModelRuntimeState.NOT_LOADED),
        )

    def assign(self, model_type: ModelType, deployment_id: str) -> dict[str, str]:
        deployment = self.get_deployment(deployment_id)
        if deployment.status is not DeploymentStatus.ACTIVE:
            raise ValueError("DEPLOYMENT_NOT_ACTIVE")
        version = self.get_version(deployment.model_version_id)
        definition = self.get_definition(version.model_definition_id)
        if definition.model_type is not model_type:
            raise ValueError("MODEL_TYPE_MISMATCH")
        if self.runtime_status(deployment_id).state is not ModelRuntimeState.ACTIVE:
            raise ValueError("MODEL_RUNTIME_NOT_ACTIVE")
        previous = self._assignments.get(model_type)
        self._assignments[model_type] = deployment_id
        self.repository.put_assignment(model_type, deployment_id)
        if model_type is ModelType.FACE_EMBEDDER and previous != deployment_id:
            self._vector_index_status = "REBUILD_REQUIRED"
        return self.assignments()

    def assignments(self) -> dict[str, str]:
        return {item.value: value for item, value in self._assignments.items()}

    def set_vector_index_ready(self) -> None:
        self._vector_index_status = "ACTIVE"

    def readiness(self) -> dict[str, object]:
        missing = [item.value for item in ModelType if item not in self._assignments]
        reasons = [f"{item}_NOT_ASSIGNED" for item in missing]
        if self._vector_index_status != "ACTIVE":
            reasons.append(f"VECTOR_INDEX_{self._vector_index_status}")
        # Assignment is control-plane intent, not a functioning recognition pipeline.
        reasons.append("FACE_RECOGNITION_PIPELINE_NOT_IMPLEMENTED")
        return {
            "status": "READY" if not reasons else "NOT_READY",
            "reasons": reasons,
            "assignments": self.assignments(),
            "vector_index_status": self._vector_index_status,
        }

    def internal_deployment(self, deployment_id: str) -> dict[str, object]:
        deployment = self.get_deployment(deployment_id)
        version = self.get_version(deployment.model_version_id)
        definition = self.get_definition(version.model_definition_id)
        artifact = self.repository.get_artifact(version.artifact_id)
        if artifact is None:
            raise ValueError("ARTIFACT_MISSING")
        return {
            "deployment": deployment,
            "version": version,
            "definition": definition,
            "artifact": {
                "id": artifact.id,
                "checksum": artifact.checksum,
                "size_bytes": artifact.size_bytes,
                "content_url": (f"/api/v1/internal/model-artifacts/{artifact.id}/content"),
            },
        }

    def artifact_content(self, artifact_id: str) -> Iterator[bytes]:
        artifact = self.repository.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        return self._ingestor.iter_bytes(artifact.storage_key)
