import os
from uuid import uuid4

import pytest
from control_api.object_storage import MinioObjectStorage


@pytest.mark.skipif(
    not all(os.getenv(name) for name in (
        "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"
    )),
    reason="local MinIO integration environment is not configured",
)
def test_minio_object_storage_integration_round_trip() -> None:
    storage = MinioObjectStorage(
        os.environ["MINIO_ENDPOINT"], os.environ["MINIO_ACCESS_KEY"],
        os.environ["MINIO_SECRET_KEY"], os.getenv("MINIO_MODELS_BUCKET", "models"),
    )
    key = f"integration/{uuid4()}.bin"
    payload = b"private object storage round trip"

    storage.put(key, payload, "application/octet-stream")
    try:
        assert storage.get(key) == payload
    finally:
        storage.delete(key)
