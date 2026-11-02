"""Idempotently create private local-development buckets in MinIO."""

import os
import time

from minio import Minio

client = Minio(
    os.environ["MINIO_ENDPOINT"],
    access_key=os.environ["MINIO_ACCESS_KEY"],
    secret_key=os.environ["MINIO_SECRET_KEY"],
    secure=False,
)

deadline = time.monotonic() + 90
while True:
    try:
        for bucket in os.environ.get("MINIO_BUCKETS", "models,face-images,evidence").split(","):
            name = bucket.strip()
            if name and not client.bucket_exists(name):
                client.make_bucket(name)
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(2)
