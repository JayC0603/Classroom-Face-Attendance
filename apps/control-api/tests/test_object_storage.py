from io import BytesIO

import pytest
from control_api.object_storage import (
    MinioObjectStorage,
    ObjectKeyInvalid,
)


class FakeResponse(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.released = False

    def release_conn(self) -> None:
        self.released = True


class FakeMinioClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, _bucket: str, key: str, stream: BytesIO, length: int,
                   content_type: str) -> None:
        assert content_type == "application/octet-stream"
        self.objects[key] = stream.read(length)

    def get_object(self, _bucket: str, key: str) -> FakeResponse:
        return FakeResponse(self.objects[key])

    def remove_object(self, _bucket: str, key: str) -> None:
        self.objects.pop(key, None)


def storage() -> MinioObjectStorage:
    return MinioObjectStorage("minio:9000", "access", "secret", "models",
                              client=FakeMinioClient())


def test_object_storage_round_trip_and_bounded_iteration() -> None:
    adapter = storage()
    payload = b"x" * (1024 * 1024 + 7)
    adapter.put("models/model.onnx", payload, "application/octet-stream")

    chunks = list(adapter.iter_bytes("models/model.onnx"))

    assert b"".join(chunks) == payload
    assert all(0 < len(chunk) <= 1024 * 1024 for chunk in chunks)
    assert adapter.get("models/model.onnx") == payload


def test_object_storage_rejects_invalid_key() -> None:
    adapter = storage()

    with pytest.raises(ObjectKeyInvalid):
        adapter.put("../private/model.onnx", b"model", "application/octet-stream")


def test_object_storage_delete_is_idempotent() -> None:
    adapter = storage()
    adapter.put("models/one", b"model", "application/octet-stream")

    adapter.delete("models/one")
    adapter.delete("models/one")


def test_object_storage_health_requires_configured_bucket() -> None:
    class MissingBucket(FakeMinioClient):
        def bucket_exists(self, _bucket: str) -> bool:
            return False

    adapter = MinioObjectStorage("minio:9000", "access", "secret", "models",
                                 client=MissingBucket())

    with pytest.raises(Exception, match="bucket"):
        adapter.health_check()
