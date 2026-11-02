"""Environment-driven MinIO storage construction for fixed private buckets."""

import os

from control_api.object_storage import MinioObjectStorage, ObjectStorage


def build_configured_object_storage(
    bucket_environment: str, default_bucket: str
) -> ObjectStorage | None:
    values = {
        "endpoint": os.getenv("MINIO_ENDPOINT"),
        "access_key": os.getenv("MINIO_ACCESS_KEY"),
        "secret_key": os.getenv("MINIO_SECRET_KEY"),
    }
    configured = [value is not None for value in values.values()]
    if not any(configured):
        return None
    if not all(configured):
        raise RuntimeError(
            "MINIO_ENDPOINT, MINIO_ACCESS_KEY, and MINIO_SECRET_KEY are required together"
        )
    bucket = os.getenv(bucket_environment, default_bucket)
    return MinioObjectStorage(
        values["endpoint"] or "",
        values["access_key"] or "",
        values["secret_key"] or "",
        bucket,
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
