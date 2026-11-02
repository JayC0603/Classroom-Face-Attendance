"""Private, fixed-bucket object storage backed by the MinIO S3 API."""

from __future__ import annotations

import re
from collections.abc import Iterator
from io import BytesIO
from typing import Protocol, cast

MAX_OBJECT_CHUNK_BYTES = 1024 * 1024
_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class ObjectStorageError(RuntimeError):
    """Base error for unavailable or invalid object storage operations."""


class ObjectKeyInvalid(ObjectStorageError, ValueError):
    """An object key is outside the allowed opaque namespace syntax."""


class ObjectNotFound(ObjectStorageError, FileNotFoundError):
    """The requested object does not exist."""


class ObjectStorageUnavailable(ObjectStorageError):
    """The configured bucket or MinIO service cannot be reached."""


class MinioClient(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...
    def put_object(self, bucket_name: str, object_name: str, data: object,
                   length: int, content_type: str) -> object: ...
    def get_object(self, bucket_name: str, object_name: str) -> MinioResponse: ...
    def remove_object(self, bucket_name: str, object_name: str) -> object: ...


class MinioResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def close(self) -> None: ...
    def release_conn(self) -> None: ...


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def iter_bytes(self, key: str, chunk_size: int = MAX_OBJECT_CHUNK_BYTES) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...
    def health_check(self) -> None: ...


def _validate_key(key: str) -> str:
    if (
        not isinstance(key, str)
        or not key
        or len(key.encode("utf-8")) > 1024
        or key.startswith("/")
        or "\\" in key
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ObjectKeyInvalid("invalid object key")
    return key


class MinioObjectStorage:
    """MinIO adapter whose bucket is fixed at construction time."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
        client: MinioClient | None = None,
    ) -> None:
        if not endpoint or not access_key or not secret_key:
            raise ObjectStorageUnavailable("MinIO connection settings are required")
        if not _BUCKET_PATTERN.fullmatch(bucket):
            raise ValueError("invalid MinIO bucket name")
        self._bucket = bucket
        if client is None:
            try:
                from minio import Minio
            except ImportError as error:  # pragma: no cover - installation error
                raise ObjectStorageUnavailable("MinIO SDK is not installed") from error
            client = cast(
                MinioClient,
                Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure),
            )
        self._client = client

    def put(self, key: str, content: bytes, content_type: str) -> None:
        key = _validate_key(key)
        if not isinstance(content, bytes) or not content_type:
            raise ValueError("content bytes and content_type are required")
        try:
            self._client.put_object(
                self._bucket, key, BytesIO(content), len(content), content_type=content_type
            )
        except Exception as error:
            raise ObjectStorageUnavailable("MinIO object upload failed") from error

    def get(self, key: str) -> bytes:
        return b"".join(self.iter_bytes(key))

    def iter_bytes(
        self, key: str, chunk_size: int = MAX_OBJECT_CHUNK_BYTES
    ) -> Iterator[bytes]:
        key = _validate_key(key)
        if not 0 < chunk_size <= MAX_OBJECT_CHUNK_BYTES:
            raise ValueError(f"chunk_size must be between 1 and {MAX_OBJECT_CHUNK_BYTES}")
        response: MinioResponse | None = None
        try:
            response = self._client.get_object(self._bucket, key)
            while chunk := response.read(chunk_size):
                yield chunk
        except Exception as error:
            if getattr(error, "code", None) in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                raise ObjectNotFound("object not found") from error
            raise ObjectStorageUnavailable("MinIO object read failed") from error
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def delete(self, key: str) -> None:
        key = _validate_key(key)
        try:
            self._client.remove_object(self._bucket, key)
        except Exception as error:
            if getattr(error, "code", None) not in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                raise ObjectStorageUnavailable("MinIO object deletion failed") from error

    def health_check(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                raise ObjectStorageUnavailable("configured MinIO bucket is missing")
        except ObjectStorageUnavailable:
            raise
        except Exception as error:
            raise ObjectStorageUnavailable("MinIO health check failed") from error
