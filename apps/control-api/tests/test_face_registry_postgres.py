"""Opt-in real database checks, confined to a newly generated test schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.face_image_storage import FaceImageStorage
from control_api.face_registry import FaceRegistryService, PersonCreate, StudentIdAlreadyExists
from control_api.face_registry_postgres import PostgresFaceRegistryRepository
from control_api.vector_index import (
    PostgresEnrollmentIndexRepository,
    initialize_vector_index_schema,
)
from fr_domain.vector_index import FaceImageRecord, PersonRecord
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def registry_database(tmp_path):
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("FR_TEST_POSTGRES_DSN required for isolated PostgreSQL registry checks")
    schema = "fr_registry_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")
    try:
        # Simulate an existing installation, including the old vector repository.
        with psycopg.connect(scoped_dsn) as connection:
            initialize_vector_index_schema(connection)
            registry_migration = (
                Path(__file__).resolve().parents[3]
                / "infrastructure/database/migrations/004_face_registry.sql"
            )
            connection.execute(registry_migration.read_text(encoding="utf-8"))
            auth_migration = (
                Path(__file__).resolve().parents[3]
                / "infrastructure/database/migrations/009_auth_accounts.sql"
            )
            connection.execute(auth_migration.read_text(encoding="utf-8"))
            review_migration = (
                Path(__file__).resolve().parents[3]
                / "infrastructure/database/migrations/011_face_enrollment_review.sql"
            )
            connection.execute(review_migration.read_text(encoding="utf-8"))
            person_number_migration = (
                Path(__file__).resolve().parents[3]
                / "infrastructure/database/migrations/012_person_numbers.sql"
            )
            connection.execute(person_number_migration.read_text(encoding="utf-8"))
            PostgresEnrollmentIndexRepository(connection).put_person(
                PersonRecord("legacy", "Legacy Person", None),
            )
        repo = PostgresFaceRegistryRepository(scoped_dsn)
        yield scoped_dsn, FaceRegistryService(repo, FaceImageStorage(tmp_path / "images"))
    finally:
        # Only this fixture's generated schema is removed, never application tables.
        assert schema.startswith("fr_registry_test_") and len(schema) == 49
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_migration_restart_legacy_inserts_and_pending_upload(registry_database):
    dsn, service = registry_database
    created = service.create_person(PersonCreate(full_name="Synthetic Test", student_id="TEST001"))
    out = BytesIO()
    Image.new("RGB", (40, 40), "blue").save(out, format="PNG")
    image = service.save_image(created.id, "face.png", out.getvalue(), source="CAMERA_CAPTURE")
    reopened = PostgresFaceRegistryRepository(dsn)  # migration is idempotent
    assert reopened.get_person(created.id).image_count == 1
    assert reopened.list_images(created.id)[0].public == image
    assert reopened.list_images(created.id)[0].public.source == "CAMERA_CAPTURE"
    assert reopened.list_persons("test001", 20, 0)[1] == 1
    with psycopg.connect(dsn) as connection:
        legacy = PostgresEnrollmentIndexRepository(connection)
        legacy.put_person(PersonRecord("legacy-2", "Old Caller", "OLD001"))
        legacy.put_face_image(FaceImageRecord("legacy-image", "legacy-2"))
        assert connection.execute(
            "SELECT active FROM fr_face_images WHERE id=%s", (image.id,)
        ).fetchone() == (False,)
        assert connection.execute("SELECT count(*) FROM fr_face_embeddings").fetchone() == (0,)
    assert reopened.get_person("legacy-2").student_id == "OLD001"


def test_postgres_face_review_is_compare_and_set_with_private_audit(registry_database):
    dsn, service = registry_database
    person = service.create_person(PersonCreate(full_name="Review Subject", student_id="REVIEW001"))
    out = BytesIO()
    Image.new("RGB", (40, 40), "green").save(out, format="PNG")
    image = service.save_image(person.id, "face.png", out.getvalue())
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO auth_users(id,email,password_hash,role) "
            "VALUES('review-admin','review-admin@example.test','test','ADMIN')"
        )

    approved = service.review_image(image.id, "review-admin", "APPROVE")
    assert approved.review_state == "APPROVED"
    with pytest.raises(KeyError, match="FACE_IMAGE_NOT_PENDING"):
        service.review_image(image.id, "review-admin", "REJECT")
    audit = service.review_audit()
    assert len(audit) == 1
    assert {key: audit[0][key] for key in ("reviewer_id", "person_id", "image_id", "decision")} == {
        "reviewer_id": "review-admin",
        "person_id": person.id,
        "image_id": image.id,
        "decision": "APPROVED",
    }
    assert audit[0]["reviewed_at"] is not None


def test_postgres_person_numbers_are_stable_and_allocated_without_failed_insert_gaps(
    registry_database,
):
    dsn, service = registry_database
    first = service.create_person(PersonCreate(full_name="First", student_id="NO-001"))
    with pytest.raises(StudentIdAlreadyExists):
        service.create_person(PersonCreate(full_name="Duplicate", student_id="NO-001"))
    second = service.create_person(PersonCreate(full_name="Second", student_id="NO-002"))

    assert (first.person_number, second.person_number) == (2, 3)
    reopened = PostgresFaceRegistryRepository(dsn)
    assert reopened.get_person(first.id).person_number == 2
    assert reopened.get_person(second.id).person_number == 3


def test_postgres_review_rejects_legacy_row_without_image_metadata(registry_database):
    dsn, service = registry_database
    person = service.create_person(PersonCreate(full_name="Legacy Subject", student_id="LEGACY001"))
    image_id = "legacy-no-file"
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO auth_users(id,email,password_hash,role) "
            "VALUES('legacy-reviewer','legacy-reviewer@example.test','test','ADMIN')"
        )
        connection.execute(
            "INSERT INTO fr_face_images(id,person_id,evidence_ref,active) "
            "VALUES(%s,%s,NULL,false)",
            (image_id, person.id),
        )

    with pytest.raises(KeyError, match="FACE_IMAGE_NOT_PENDING"):
        service.review_image(image_id, "legacy-reviewer", "APPROVE")
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT review_state FROM fr_face_images WHERE id=%s", (image_id,)
        ).fetchone() == ("PENDING_REVIEW",)
        assert connection.execute(
            "SELECT count(*) FROM fr_face_image_review_audit WHERE image_id=%s", (image_id,)
        ).fetchone() == (0,)


def test_postgres_concurrent_unique_id_and_failed_transaction_recovery(registry_database):
    dsn, service = registry_database

    def create(index):
        try:
            return service.create_person(
                PersonCreate(
                    full_name=f"Synthetic {index}",
                    student_id="Concurrent" if index % 2 else "concurrent",
                )
            )
        except StudentIdAlreadyExists:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert sum(value is not None for value in results) == 1
    repo = PostgresFaceRegistryRepository(dsn)
    assert repo.list_persons("concurrent", 20, 0)[1] == 1
    assert service.create_person(PersonCreate(full_name="After conflict", student_id="NEXT")).id


def test_camera_source_migration_preserves_all_sources_and_legacy_rows(registry_database):
    from control_api.camera_postgres import PostgresCameraRepository, initialize_camera_schema
    from control_api.cameras import (
        CameraCreate,
        CameraService,
        CameraUpdate,
        FernetCameraSecretProtector,
    )

    dsn, _ = registry_database
    protector = FernetCameraSecretProtector.generate()
    with psycopg.connect(dsn) as connection:
        initialize_camera_schema(connection)
        initialize_camera_schema(connection)
        service = CameraService(PostgresCameraRepository(connection), protector)
        camera = service.create(CameraCreate(name="Synthetic browser", source_type="webcam"))
        assert service.require(camera.id).source_type == "webcam"
        local = service.create(
            CameraCreate(name="Synthetic local", source_type="local_webcam", device_index=2)
        )
        # An installation predating the new source table stored browser sources here.
        connection.execute(
            "INSERT INTO camera_source_settings (camera_id,source_type) VALUES (%s,'webcam')",
            (camera.id,),
        )
        connection.execute("DELETE FROM camera_source_configs WHERE camera_id=%s", (camera.id,))
        connection.commit()
        initialize_camera_schema(connection)
    with psycopg.connect(dsn) as connection:
        service = CameraService(PostgresCameraRepository(connection), protector)
        assert service.require(camera.id).source_type == "webcam"
        assert service.require(local.id).source_type == "local_webcam"
        assert service.require(local.id).device_index == 2
        initialize_camera_schema(connection)  # legacy backfill never overwrites local settings
        assert service.require(local.id).device_index == 2
        changed = service.update(
            camera.id,
            CameraUpdate(
                source_type="rtsp",
                rtsp_url="rtsp://camera.local/live",
            ),
        )
        assert changed.source_type == "rtsp"
        assert service.require(camera.id).protected_rtsp_url
        service.update(local.id, CameraUpdate(source_type="webcam"))
        assert service.require(local.id).device_index is None


def test_camera_source_schema_startup_does_not_block_existing_readers(registry_database):
    from control_api.camera_postgres import initialize_camera_schema

    dsn, _ = registry_database
    with psycopg.connect(dsn) as connection:
        initialize_camera_schema(connection)
    with psycopg.connect(dsn) as reader:
        reader.execute("SELECT * FROM cameras").fetchall()
        with psycopg.connect(dsn) as starter:
            starter.execute("SET lock_timeout='200ms'")
            initialize_camera_schema(starter)
