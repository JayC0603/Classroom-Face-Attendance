from hashlib import sha256
from pathlib import Path

import pytest
from control_api import model_bootstrap
from control_api.model_artifacts import ModelArtifact
from control_api.model_domain import (
    ArtifactFormat,
    ModelDefinition,
    ModelType,
    ModelVersion,
    ValidationStatus,
)
from control_api.model_registry import InMemoryModelRepository
from test_model_domain import person_manifest


def test_explicit_postgres_failure_does_not_fallback_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FR_POSTGRES_DSN", "postgresql://db.invalid/face")

    def unavailable(_dsn: str) -> object:
        raise OSError("database unavailable")

    monkeypatch.setattr(model_bootstrap, "connect_postgres", unavailable)

    with pytest.raises(OSError, match="database unavailable"):
        model_bootstrap.build_model_registry()


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, content: bytes, content_type: str) -> None:
        assert content_type == "application/onnx"
        self.objects[key] = content

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def _legacy_registry(source: Path, checksum: str, size: int) -> tuple[InMemoryModelRepository, str]:
    repository = InMemoryModelRepository()
    definition = ModelDefinition.create("YOLOX Person", ModelType.PERSON_DETECTOR, "ONNX")
    artifact = ModelArtifact("artifact-1", checksum, size, "ONNX", source.as_posix())
    version = ModelVersion.create(
        definition.id,
        "v1",
        artifact.id,
        ArtifactFormat.ONNX,
        checksum,
        size,
        person_manifest(),
        ValidationStatus.VALID,
    )
    repository.put_definition(definition)
    repository.put_artifact_and_version(artifact, version)
    return repository, version.id


def test_legacy_local_model_artifact_moves_to_minio_after_checksum_verification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.onnx"
    payload = b"original model bytes"
    source.write_bytes(payload)
    checksum = sha256(payload).hexdigest()
    repository, _version_id = _legacy_registry(source, checksum, len(payload))
    storage = MemoryObjectStorage()

    assert model_bootstrap.migrate_legacy_model_artifacts(repository, storage) == 1

    artifact = repository.get_artifact("artifact-1")
    assert artifact is not None
    assert artifact.storage_key == "artifact-1.onnx"
    assert storage.objects[artifact.storage_key] == payload
    assert source.read_bytes() == payload
    assert model_bootstrap.migrate_legacy_model_artifacts(repository, storage) == 0


def test_legacy_local_model_artifact_with_wrong_checksum_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "legacy.onnx"
    source.write_bytes(b"changed model bytes")
    repository, _version_id = _legacy_registry(source, "0" * 64, source.stat().st_size)
    storage = MemoryObjectStorage()

    with pytest.raises(RuntimeError, match="LEGACY_MODEL_ARTIFACT_CHECKSUM_MISMATCH"):
        model_bootstrap.migrate_legacy_model_artifacts(repository, storage)

    assert storage.objects == {}
    assert repository.get_artifact("artifact-1").storage_key == source.as_posix()


def test_unreferenced_legacy_artifact_is_also_migrated(tmp_path: Path) -> None:
    source = tmp_path / "orphan.onnx"
    payload = b"orphaned but retained model"
    source.write_bytes(payload)
    repository = InMemoryModelRepository()
    repository.put_artifact(
        ModelArtifact(
            "orphan-artifact", sha256(payload).hexdigest(), len(payload), "ONNX", source.as_posix()
        )
    )
    storage = MemoryObjectStorage()

    assert model_bootstrap.migrate_legacy_model_artifacts(repository, storage) == 1
    artifact = repository.get_artifact("orphan-artifact")
    assert artifact is not None and artifact.storage_key == "orphan-artifact.onnx"
    assert storage.objects[artifact.storage_key] == payload
