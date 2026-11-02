import pytest
from fr_domain.model_management import DeploymentBackend, ModelType
from vision_worker.model_runtime import (
    AtomicModelSet,
    InferenceRuntimeResolver,
    ModelArtifacts,
    RuntimeCapabilities,
    RuntimeResolutionError,
)


def test_auto_prefers_compatible_acceleration_then_cpu_with_observable_fallback() -> None:
    resolver = InferenceRuntimeResolver()
    capabilities = RuntimeCapabilities(
        onnx_cpu_available=True,
        onnx_cuda_available=True,
        tensorrt_available=True,
        execution_providers=("CUDAExecutionProvider", "CPUExecutionProvider"),
    )
    resolved = resolver.resolve(
        DeploymentBackend.AUTO,
        ModelArtifacts(onnx=True, compatible_tensorrt=False),
        capabilities,
    )
    assert resolved.backend is DeploymentBackend.ONNX_CUDA
    assert resolved.execution_provider == "CUDAExecutionProvider"
    assert resolved.fallback_reason == "TENSORRT_ARTIFACT_UNAVAILABLE"

    cpu = resolver.resolve(
        DeploymentBackend.AUTO,
        ModelArtifacts(onnx=True),
        RuntimeCapabilities.cpu_only(),
    )
    assert cpu.backend is DeploymentBackend.ONNX_CPU
    assert cpu.fallback_reason == "CUDA_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("backend", "category"),
    [
        (DeploymentBackend.ONNX_CUDA, "CUDA_UNAVAILABLE"),
        (DeploymentBackend.TENSORRT, "TENSORRT_UNAVAILABLE"),
    ],
)
def test_explicit_accelerated_backend_is_strict(backend: DeploymentBackend, category: str) -> None:
    with pytest.raises(RuntimeResolutionError, match=category):
        InferenceRuntimeResolver().resolve(
            backend,
            ModelArtifacts(onnx=True),
            RuntimeCapabilities.cpu_only(),
        )


def test_explicit_cpu_requires_onnx_and_resolves_without_gpu() -> None:
    resolved = InferenceRuntimeResolver().resolve(
        DeploymentBackend.ONNX_CPU,
        ModelArtifacts(onnx=True),
        RuntimeCapabilities.cpu_only(),
    )
    assert resolved.backend is DeploymentBackend.ONNX_CPU
    assert resolved.execution_provider == "CPUExecutionProvider"


class Runtime:
    def __init__(self, deployment_id: str, warmup_ok: bool = True) -> None:
        self.deployment_id = deployment_id
        self.model_type = ModelType.PERSON_DETECTOR
        self.warmup_ok = warmup_ok
        self.closed = False

    def warmup(self) -> None:
        if not self.warmup_ok:
            raise RuntimeError("MODEL_WARMUP_FAILED")

    def health_check(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_atomic_activation_retains_previous_runtime_after_warmup_failure() -> None:
    model_set = AtomicModelSet()
    old = Runtime("old")
    model_set.activate(old)
    replacement = Runtime("replacement", warmup_ok=False)

    with pytest.raises(RuntimeError, match="MODEL_WARMUP_FAILED"):
        model_set.activate(replacement)

    assert model_set.active(ModelType.PERSON_DETECTOR) is old
    assert old.closed is False
    assert replacement.closed is True


def test_successful_atomic_swap_closes_previous_after_new_runtime_is_healthy() -> None:
    model_set = AtomicModelSet()
    old = Runtime("old")
    replacement = Runtime("replacement")
    model_set.activate(old)
    model_set.activate(replacement)
    assert model_set.active(ModelType.PERSON_DETECTOR) is replacement
    assert old.closed is True


def test_deferred_retirement_keeps_old_runtime_until_reader_replaced() -> None:
    model_set = AtomicModelSet(defer_retirement=True)
    old = Runtime("old")
    replacement = Runtime("replacement")
    model_set.activate(old)
    model_set.activate(replacement)
    assert model_set.active(ModelType.PERSON_DETECTOR) is replacement
    assert old.closed is False
    model_set.close_retired()
    assert old.closed is True
    assert replacement.closed is False
    model_set.close_all()
    assert replacement.closed is True


def test_close_all_releases_active_runtime() -> None:
    model_set = AtomicModelSet()
    runtime = Runtime("current")
    model_set.activate(runtime)
    model_set.close_all()
    assert runtime.closed is True
    assert model_set.active(ModelType.PERSON_DETECTOR) is None
