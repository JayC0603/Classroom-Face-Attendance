from pathlib import Path

import pytest
from control_api.model_artifacts import (
    LocalModelArtifactStorage,
    ModelArtifact,
    ModelArtifactIngestor,
)
from control_api.model_registry import InMemoryModelRepository, ModelRegistryService
from fr_domain.model_management import (
    ArtifactFormat,
    DeploymentBackend,
    DeploymentStatus,
    ModelManifest,
    ModelType,
    ModelVersion,
    TensorSpec,
    ValidationStatus,
)
from test_model_artifacts import Inspector
from test_model_domain import person_manifest


def service(tmp_path: Path) -> ModelRegistryService:
    return ModelRegistryService(
        InMemoryModelRepository(),
        ModelArtifactIngestor(LocalModelArtifactStorage(tmp_path), Inspector(), 1024),
    )


def test_model_ingest_deletes_object_when_metadata_commit_fails(tmp_path: Path) -> None:
    class FailingRepository(InMemoryModelRepository):
        def put_artifact_and_version(
            self, _artifact: ModelArtifact, _version: ModelVersion
        ) -> None:
            raise RuntimeError("metadata commit failed")

    storage = LocalModelArtifactStorage(tmp_path)
    registry = ModelRegistryService(
        FailingRepository(), ModelArtifactIngestor(storage, Inspector(), 1024)
    )
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")

    with pytest.raises(RuntimeError, match="metadata commit failed"):
        registry.create_version(
            definition.id, "v1", "model.onnx", b"controlled-onnx", person_manifest()
        )

    assert list(tmp_path.rglob("model.onnx")) == []


def test_model_ingest_persists_artifact_and_version_atomically(tmp_path: Path) -> None:
    repository = InMemoryModelRepository()
    registry = ModelRegistryService(
        repository,
        ModelArtifactIngestor(LocalModelArtifactStorage(tmp_path), Inspector(), 1024),
    )
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")

    version = registry.create_version(
        definition.id, "v1", "model.onnx", b"controlled-onnx", person_manifest()
    )

    artifact = repository.get_artifact(version.artifact_id)
    stored_version = repository.get_version(version.id)
    assert artifact is not None and stored_version == version
    assert artifact.id == version.artifact_id
    assert artifact.checksum == version.checksum


def test_definition_crud_and_version_immutability(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO Person", ModelType.PERSON_DETECTOR, "ONNX")
    version = registry.create_version(
        definition.id,
        "v1",
        "model.onnx",
        b"controlled-onnx-v1",
        person_manifest(),
    )
    assert version.validation_status is ValidationStatus.VALID
    assert registry.list_definitions() == [definition]
    assert registry.list_versions(definition.id) == [version]
    with pytest.raises(ValueError, match="MODEL_VERSION_EXISTS"):
        registry.create_version(
            definition.id,
            "v1",
            "model.onnx",
            b"controlled-onnx-v2",
            person_manifest(),
        )


def test_activation_result_atomically_replaces_same_role(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")
    v1 = registry.create_version(
        definition.id, "v1", "model.onnx", b"controlled-onnx-1", person_manifest()
    )
    v2 = registry.create_version(
        definition.id, "v2", "model.onnx", b"controlled-onnx-2", person_manifest()
    )
    d1 = registry.create_deployment(v1.id, DeploymentBackend.ONNX_CPU, "FP32")
    d2 = registry.create_deployment(v2.id, DeploymentBackend.AUTO, "FP32")
    registry.request_activation(d1.id)
    registry.record_activation(d1.id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU)
    registry.request_activation(d2.id)
    registry.record_activation(d2.id, success=False, error_category="MODEL_WARMUP_FAILED")
    assert registry.get_deployment(d1.id).status is DeploymentStatus.ACTIVE
    assert registry.get_deployment(d2.id).status is DeploymentStatus.ERROR

    registry.request_activation(d2.id)
    registry.record_activation(d2.id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU)
    assert registry.get_deployment(d1.id).status is DeploymentStatus.INACTIVE
    assert registry.get_deployment(d2.id).status is DeploymentStatus.ACTIVE


def test_invalid_version_cannot_create_deployment(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")
    version = ModelVersion.create(
        definition.id,
        "bad",
        "artifact-bad",
        ArtifactFormat.ONNX,
        "a" * 64,
        1,
        person_manifest(),
        ValidationStatus.INVALID,
        "INVALID_MODEL_FORMAT",
    )
    registry.repository.put_version(version)
    with pytest.raises(ValueError, match="MODEL_VERSION_NOT_VALID"):
        registry.create_deployment(version.id, DeploymentBackend.AUTO, "FP32")


def test_active_artifact_cannot_be_deleted(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")
    version = registry.create_version(
        definition.id, "v1", "model.onnx", b"controlled-onnx", person_manifest()
    )
    deployment = registry.create_deployment(version.id, DeploymentBackend.ONNX_CPU, "FP32")
    registry.request_activation(deployment.id)
    registry.record_activation(
        deployment.id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU
    )
    with pytest.raises(ValueError, match="ARTIFACT_IN_USE"):
        registry.delete_version(version.id)


def test_runtime_status_is_not_inferred_from_database_status(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")
    version = registry.create_version(
        definition.id, "v1", "model.onnx", b"controlled-onnx", person_manifest()
    )
    deployment = registry.create_deployment(version.id, DeploymentBackend.AUTO, "FP32")
    assert registry.runtime_status(deployment.id).state.value == "NOT_LOADED"
    registry.request_activation(deployment.id)
    assert registry.runtime_status(deployment.id).state.value == "LOADING"
    registry.record_activation(
        deployment.id,
        success=True,
        resolved_backend=DeploymentBackend.ONNX_CPU,
        execution_provider="CPUExecutionProvider",
        fallback_reason="CUDA_PROVIDER_UNAVAILABLE",
    )
    status = registry.runtime_status(deployment.id)
    assert status.state.value == "ACTIVE"
    assert status.execution_provider == "CPUExecutionProvider"


def test_assignment_requires_healthy_matching_runtime_and_embedder_invalidates_index(
    tmp_path: Path,
) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("YOLO", ModelType.PERSON_DETECTOR, "ONNX")
    version = registry.create_version(
        definition.id, "v1", "model.onnx", b"controlled-onnx", person_manifest()
    )
    deployment = registry.create_deployment(version.id, DeploymentBackend.ONNX_CPU, "FP32")
    with pytest.raises(ValueError, match="DEPLOYMENT_NOT_ACTIVE"):
        registry.assign(ModelType.PERSON_DETECTOR, deployment.id)
    registry.request_activation(deployment.id)
    registry.record_activation(
        deployment.id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU
    )
    assert registry.assign(ModelType.PERSON_DETECTOR, deployment.id) == {
        "PERSON_DETECTOR": deployment.id
    }
    with pytest.raises(ValueError, match="MODEL_TYPE_MISMATCH"):
        registry.assign(ModelType.FACE_DETECTOR, deployment.id)
    assert registry.readiness()["status"] == "NOT_READY"
    assert "FACE_RECOGNITION_PIPELINE_NOT_IMPLEMENTED" in registry.readiness()["reasons"]


def embedder_manifest() -> ModelManifest:
    return ModelManifest(
        model_type=ModelType.FACE_EMBEDDER,
        inputs=[TensorSpec(name="input", shape=[1, 3, 112, 112], dtype="float32")],
        outputs=[TensorSpec(name="embedding", shape=[1, 512], dtype="float32")],
        preprocessing={
            "layout": "NCHW",
            "color_order": "RGB",
            "normalization": "adaface_v1",
            "resize_mode": "aligned",
        },
        metadata={
            "architecture": "AdaFace",
            "embedding_dimension": 512,
            "l2_normalized": True,
            "distance_metric": "cosine",
            "alignment_profile": "arcface_5point_112",
        },
    )


def test_embedder_version_change_marks_vector_index_for_rebuild(tmp_path: Path) -> None:
    registry = service(tmp_path)
    definition = registry.create_definition("AdaFace", ModelType.FACE_EMBEDDER, "ONNX")
    versions = [
        registry.create_version(
            definition.id,
            label,
            "model.onnx",
            f"controlled-onnx-{label}".encode(),
            embedder_manifest(),
        )
        for label in ("v1", "v2")
    ]
    deployments = [
        registry.create_deployment(version.id, DeploymentBackend.ONNX_CPU, "FP32")
        for version in versions
    ]
    for deployment in deployments:
        registry.request_activation(deployment.id)
        registry.record_activation(
            deployment.id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU
        )
    registry.assign(ModelType.FACE_EMBEDDER, deployments[1].id)
    registry.set_vector_index_ready()
    registry.assign(ModelType.FACE_EMBEDDER, deployments[1].id)
    assert registry.readiness()["vector_index_status"] == "ACTIVE"

    registry.request_activation(deployments[0].id)
    registry.record_activation(
        deployments[0].id, success=True, resolved_backend=DeploymentBackend.ONNX_CPU
    )
    registry.assign(ModelType.FACE_EMBEDDER, deployments[0].id)
    assert registry.readiness()["vector_index_status"] == "REBUILD_REQUIRED"
