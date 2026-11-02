from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from fr_domain.model_management import DeploymentBackend, ModelType


class RuntimeResolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    onnx_cpu_available: bool
    onnx_cuda_available: bool
    tensorrt_available: bool
    execution_providers: tuple[str, ...] = ()

    @classmethod
    def cpu_only(cls) -> "RuntimeCapabilities":
        return cls(True, False, False, ("CPUExecutionProvider",))


@dataclass(frozen=True, slots=True)
class ModelArtifacts:
    onnx: bool = False
    compatible_tensorrt: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedModelRuntime:
    requested_backend: DeploymentBackend
    backend: DeploymentBackend
    execution_provider: str
    fallback_reason: str | None = None


class InferenceRuntimeResolver:
    def resolve(
        self,
        requested: DeploymentBackend,
        artifacts: ModelArtifacts,
        capabilities: RuntimeCapabilities,
        supported_backends: frozenset[DeploymentBackend] | None = None,
    ) -> ResolvedModelRuntime:
        supported = supported_backends or frozenset(
            {DeploymentBackend.ONNX_CPU, DeploymentBackend.ONNX_CUDA, DeploymentBackend.TENSORRT}
        )
        if requested is not DeploymentBackend.AUTO and requested not in supported:
            raise RuntimeResolutionError("ADAPTER_UNAVAILABLE")
        if requested is DeploymentBackend.ONNX_CPU:
            if not artifacts.onnx:
                raise RuntimeResolutionError("ARTIFACT_MISSING")
            if not capabilities.onnx_cpu_available:
                raise RuntimeResolutionError("RUNTIME_UNAVAILABLE")
            return ResolvedModelRuntime(requested, requested, "CPUExecutionProvider")
        if requested is DeploymentBackend.ONNX_CUDA:
            if not capabilities.onnx_cuda_available:
                raise RuntimeResolutionError("CUDA_UNAVAILABLE")
            if not artifacts.onnx:
                raise RuntimeResolutionError("ARTIFACT_MISSING")
            return ResolvedModelRuntime(requested, requested, "CUDAExecutionProvider")
        if requested is DeploymentBackend.TENSORRT:
            if not capabilities.tensorrt_available:
                raise RuntimeResolutionError("TENSORRT_UNAVAILABLE")
            if not artifacts.compatible_tensorrt:
                raise RuntimeResolutionError("ENGINE_INCOMPATIBLE")
            return ResolvedModelRuntime(requested, requested, "TensorRT")

        fallback_reason: str | None = None
        if (
            DeploymentBackend.TENSORRT in supported
            and capabilities.tensorrt_available
            and artifacts.compatible_tensorrt
        ):
            return ResolvedModelRuntime(requested, DeploymentBackend.TENSORRT, "TensorRT")
        fallback_reason = (
            "TENSORRT_ADAPTER_UNAVAILABLE"
            if DeploymentBackend.TENSORRT not in supported
            else "TENSORRT_ARTIFACT_UNAVAILABLE"
            if capabilities.tensorrt_available
            else "TENSORRT_UNAVAILABLE"
        )
        if (
            DeploymentBackend.ONNX_CUDA in supported
            and capabilities.onnx_cuda_available
            and artifacts.onnx
        ):
            return ResolvedModelRuntime(
                requested,
                DeploymentBackend.ONNX_CUDA,
                "CUDAExecutionProvider",
                fallback_reason,
            )
        if DeploymentBackend.ONNX_CUDA not in supported:
            fallback_reason = "CUDA_ADAPTER_UNAVAILABLE"
        elif not capabilities.onnx_cuda_available:
            fallback_reason = "CUDA_PROVIDER_UNAVAILABLE"
        if (
            DeploymentBackend.ONNX_CPU in supported
            and capabilities.onnx_cpu_available
            and artifacts.onnx
        ):
            return ResolvedModelRuntime(
                requested,
                DeploymentBackend.ONNX_CPU,
                "CPUExecutionProvider",
                fallback_reason,
            )
        raise RuntimeResolutionError("NO_COMPATIBLE_RUNTIME")


class PreparedModelRuntime(Protocol):
    deployment_id: str
    model_type: ModelType

    def warmup(self) -> None: ...
    def health_check(self) -> None: ...
    def close(self) -> None: ...


class AtomicModelSet:
    def __init__(self, *, defer_retirement: bool = False) -> None:
        self._lock = Lock()
        self._active: dict[ModelType, PreparedModelRuntime] = {}
        self._retired: list[PreparedModelRuntime] = []
        self._defer_retirement = defer_retirement

    def active(self, model_type: ModelType) -> PreparedModelRuntime | None:
        with self._lock:
            return self._active.get(model_type)

    def activate(self, candidate: PreparedModelRuntime) -> None:
        try:
            candidate.warmup()
            candidate.health_check()
        except Exception:
            candidate.close()
            raise
        with self._lock:
            previous = self._active.get(candidate.model_type)
            self._active[candidate.model_type] = candidate
        if previous is not None and previous is not candidate:
            if self._defer_retirement:
                with self._lock:
                    self._retired.append(previous)
            else:
                previous.close()

    def close_retired(self) -> None:
        """Call only after all readers of replaced runtimes have stopped."""
        with self._lock:
            retired = self._retired
            self._retired = []
        for runtime in retired:
            runtime.close()

    def close_all(self) -> None:
        with self._lock:
            active = list(self._active.values())
            self._active.clear()
            retired = self._retired
            self._retired = []
        for runtime in active + retired:
            runtime.close()
