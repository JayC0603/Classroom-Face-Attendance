import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path, PureWindowsPath

from control_api.camera_postgres import connect_postgres
from control_api.model_artifacts import (
    LocalModelArtifactStorage,
    ModelArtifactIngestor,
    ObjectStoreModelArtifactStorage,
    OnnxArtifactInspector,
)
from control_api.model_postgres import PostgresModelRepository, initialize_model_schema
from control_api.model_registry import (
    InMemoryModelRepository,
    ModelRegistryService,
    ModelRepository,
)
from control_api.object_storage import ObjectStorage
from control_api.storage_bootstrap import build_configured_object_storage


def migrate_legacy_model_artifacts(repository: ModelRepository, storage: ObjectStorage) -> int:
    """Move verified local-path model artifacts to private object storage once."""
    migrated = 0
    for artifact in repository.list_artifacts():
        storage_key = artifact.storage_key
        if not Path(storage_key).is_absolute() and not PureWindowsPath(storage_key).is_absolute():
            continue
        source = Path(storage_key)
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise RuntimeError("LEGACY_MODEL_ARTIFACT_UNAVAILABLE") from error
        if len(payload) != artifact.size_bytes or sha256(payload).hexdigest() != artifact.checksum:
            raise RuntimeError("LEGACY_MODEL_ARTIFACT_CHECKSUM_MISMATCH")
        destination_key = f"{artifact.id}.onnx"
        storage.put(destination_key, payload, "application/onnx")
        try:
            repository.put_artifact(replace(artifact, storage_key=destination_key))
        except Exception:
            try:
                storage.delete(destination_key)
            except Exception:
                pass
            raise
        migrated += 1
    return migrated


def build_model_registry(artifact_storage: ObjectStorage | None = None) -> ModelRegistryService:
    root = Path(os.getenv("FR_MODEL_ARTIFACT_ROOT", "data/models"))
    maximum = int(os.getenv("FR_MODEL_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
    object_storage = artifact_storage or build_configured_object_storage(
        "MINIO_MODELS_BUCKET", "models"
    )
    dsn = os.getenv("FR_POSTGRES_DSN")
    if dsn:
        connection = connect_postgres(dsn)
        initialize_model_schema(connection)
        repository: ModelRepository = PostgresModelRepository(connection)
    else:
        repository = InMemoryModelRepository()
    if object_storage is not None:
        migrate_legacy_model_artifacts(repository, object_storage)
    return ModelRegistryService(
        repository,
        ModelArtifactIngestor(
            ObjectStoreModelArtifactStorage(object_storage)
            if object_storage is not None
            else LocalModelArtifactStorage(root),
            OnnxArtifactInspector(),
            maximum,
        ),
    )
