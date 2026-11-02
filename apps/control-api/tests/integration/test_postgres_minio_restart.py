"""Opt-in persistence check against isolated PostgreSQL and private MinIO buckets."""

from __future__ import annotations

import os
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.camera_postgres import PostgresCameraRepository
from control_api.cameras import CameraCreate, CameraService, FernetCameraSecretProtector
from control_api.database_migrations import apply_database_migrations
from control_api.face_image_storage import FaceImageStorage
from control_api.face_registry import FaceRegistryService, PersonCreate
from control_api.face_registry_postgres import PostgresFaceRegistryRepository
from control_api.model_artifacts import ModelArtifact
from control_api.model_domain import (
    ArtifactFormat,
    DeploymentBackend,
    DeploymentStatus,
    ModelDefinition,
    ModelDeployment,
    ModelType,
    ModelVersion,
    ValidationStatus,
)
from control_api.model_postgres import PostgresModelRepository
from control_api.object_storage import MinioObjectStorage
from control_api.recognition_events import RecognitionEventStore
from fr_domain.model_management import ModelManifest
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def persistence_services(tmp_path: Path):
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    endpoint = os.getenv("MINIO_ENDPOINT")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    if not all((dsn, endpoint, access_key, secret_key)):
        pytest.skip("FR_TEST_POSTGRES_DSN and MinIO settings required")
    suffix = uuid4().hex
    schema = "fr_restart_test_" + suffix
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")
    stores = {
        role: MinioObjectStorage(
            endpoint,
            access_key,
            secret_key,
            os.getenv(f"MINIO_{role.upper()}_BUCKET", fallback),
        )
        for role, fallback in (
            ("models", "models"),
            ("face_images", "face-images"),
            ("evidence", "evidence"),
        )
    }
    for store in stores.values():
        store.health_check()
    objects: list[tuple[MinioObjectStorage, str]] = []
    try:
        with psycopg.connect(scoped_dsn) as connection:
            apply_database_migrations(connection, Path("infrastructure/database/migrations"))
        yield scoped_dsn, stores, objects, tmp_path
    finally:
        for store, key in objects:
            store.delete(key)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_and_minio_records_survive_repository_reconstruction(persistence_services):
    scoped_dsn, stores, objects, tmp_path = persistence_services
    model_bytes = b"synthetic restart-model artifact"
    model_key = f"restart-tests/{uuid4().hex}.onnx"
    stores["models"].put(model_key, model_bytes, "application/onnx")
    objects.append((stores["models"], model_key))
    with psycopg.connect(scoped_dsn) as connection:
        model_repository = PostgresModelRepository(connection)
        definition = ModelDefinition.create("Restart test YOLOX", ModelType.PERSON_DETECTOR, "ONNX")
        artifact = ModelArtifact(
            str(uuid4()), sha256(model_bytes).hexdigest(), len(model_bytes), "ONNX", model_key
        )
        version = ModelVersion.create(
            definition.id,
            "integration-v1",
            artifact.id,
            ArtifactFormat.ONNX,
            artifact.checksum,
            artifact.size_bytes,
            ModelManifest.model_validate(
                {
                    "model_type": "PERSON_DETECTOR",
                    "inputs": [{"name": "input", "shape": [1, 3, 640, 640], "dtype": "float32"}],
                    "outputs": [{"name": "output", "shape": [1, 84, 6], "dtype": "float32"}],
                    "preprocessing": {
                        "layout": "NCHW",
                        "color_order": "RGB",
                        "normalization": "zero_to_one",
                        "resize_mode": "letterbox",
                    },
                    "metadata": {
                        "architecture": "YOLO",
                        "output_semantics": "yolo_detection",
                        "person_class_id": 0,
                    },
                }
            ),
            ValidationStatus.VALID,
        )
        model_repository.put_definition(definition)
        model_repository.put_artifact_and_version(artifact, version)
        deployment = replace(
            ModelDeployment.create(version.id, DeploymentBackend.ONNX_CPU, "FP32"),
            status=DeploymentStatus.ACTIVE,
            resolved_backend=DeploymentBackend.ONNX_CPU,
        )
        model_repository.put_deployment(deployment)
        model_repository.put_assignment(ModelType.PERSON_DETECTOR, deployment.id)

        camera_service = CameraService(
            PostgresCameraRepository(connection), FernetCameraSecretProtector.generate()
        )
        camera = camera_service.create(
            CameraCreate(
                name="Synthetic restart camera", source_type="local_webcam", device_index=0
            )
        )

    face_repository = PostgresFaceRegistryRepository(scoped_dsn)
    face_service = FaceRegistryService(
        face_repository, FaceImageStorage(None, object_storage=stores["face_images"])
    )
    person = face_service.create_person(
        PersonCreate(full_name="Synthetic Person", student_id="RST001")
    )
    image_buffer = BytesIO()
    Image.new("RGB", (40, 40), "blue").save(image_buffer, format="PNG")
    face_image = face_service.save_image(person.id, "reference.png", image_buffer.getvalue())
    face_image_key = face_repository.list_images(person.id)[0].storage_key
    objects.append((stores["face_images"], face_image_key))

    event_connection = psycopg.connect(scoped_dsn)
    event_store = RecognitionEventStore(connection=event_connection, storage=stores["evidence"])
    evidence = b"synthetic-jpeg-evidence"
    event = event_store.record_verified(
        camera_id=camera.id,
        camera_name=camera.name,
        session_id="restart-session",
        track_id=17,
        person_id=person.id,
        full_name=person.full_name,
        student_id=person.student_id,
        evidence=evidence,
        retention_days=30,
        attendance_enabled=True,
    )
    assert event is not None
    evidence_key = str(event["evidence_key"])
    objects.append((stores["evidence"], evidence_key))
    event_store.close()
    event_connection.close()

    # Re-open repositories and connections as if the API process had restarted.
    with psycopg.connect(scoped_dsn) as connection:
        reopened_models = PostgresModelRepository(connection)
        assert reopened_models.get_version(version.id) == version
        assert reopened_models.list_assignments()[ModelType.PERSON_DETECTOR] == deployment.id
        assert stores["models"].get(model_key) == model_bytes
        reopened_camera = CameraService(
            PostgresCameraRepository(connection), FernetCameraSecretProtector.generate()
        ).require(camera.id)
        assert reopened_camera.name == "Synthetic restart camera"
        assert reopened_camera.device_index == 0
    reopened_faces = PostgresFaceRegistryRepository(scoped_dsn)
    assert reopened_faces.get_person(person.id).image_count == 1
    assert stores["face_images"].get(face_image_key)
    assert face_image.source == "MANUAL_UPLOAD"

    reopened_event_connection = psycopg.connect(scoped_dsn)
    reopened_events = RecognitionEventStore(
        connection=reopened_event_connection, storage=stores["evidence"]
    )
    assert len(reopened_events.history(person_id=person.id)) == 1
    assert reopened_events.attendance_rows(person_id=person.id)[0]["event_id"] == event["id"]
    assert reopened_events.evidence_content(evidence_key) == evidence
    reopened_events.close()
    reopened_event_connection.close()
