"""Opt-in role, enrollment, review, and class-attendance journey on disposable services."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from control_api.auth_domain import AuthService
from control_api.auth_postgres import PostgresAuthRepository
from control_api.database_migrations import apply_database_migrations
from control_api.main import create_app
from control_api.object_storage import MinioObjectStorage
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def role_client(monkeypatch: pytest.MonkeyPatch):
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    endpoint = os.getenv("MINIO_ENDPOINT")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    if not all((dsn, endpoint, access_key, secret_key)):
        pytest.skip("FR_TEST_POSTGRES_DSN and private MinIO settings required")

    suffix = uuid4().hex
    schema = "fr_role_e2e_" + suffix
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")
    monkeypatch.setenv("FR_POSTGRES_DSN", scoped_dsn)
    monkeypatch.setenv("FR_AUTH_RATE_LIMIT_KEY", "integration-test-rate-limit-key-32")
    monkeypatch.setenv("FR_CAMERA_ENCRYPTION_KEY", Fernet.generate_key().decode())

    stores = {
        name: MinioObjectStorage(
            endpoint,
            access_key,
            secret_key,
            os.getenv(env_name, default),
        )
        for name, env_name, default in (
            ("models", "MINIO_MODELS_BUCKET", "models"),
            ("faces", "MINIO_FACE_IMAGES_BUCKET", "face-images"),
            ("evidence", "MINIO_EVIDENCE_BUCKET", "evidence"),
        )
    }
    for store in stores.values():
        store.health_check()
    object_keys: list[tuple[MinioObjectStorage, str]] = []
    client: TestClient | None = None
    try:
        with psycopg.connect(scoped_dsn) as connection:
            apply_database_migrations(connection, Path("infrastructure/database/migrations"))

        AuthService(PostgresAuthRepository(scoped_dsn)).bootstrap_admin(
            "admin-" + suffix + "@example.test", "Admin password 9!"
        )
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        yield client, stores, object_keys, suffix
    finally:
        if client is not None:
            client.__exit__(None, None, None)
        for store, key in object_keys:
            store.delete(key)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def sign_in(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}
    )


def test_admin_student_teacher_enrollment_review_and_attendance(role_client):
    client, stores, object_keys, suffix = role_client
    admin_email = "admin-" + suffix + "@example.test"
    teacher_email = "teacher-" + suffix + "@example.test"
    student_email = "student-" + suffix + "@example.test"

    sign_in(client, admin_email, "Admin password 9!")
    created_camera = client.post(
        "/api/v1/cameras",
        json={"name": "Integration camera", "source_type": "local_webcam", "device_index": 0},
    )
    assert created_camera.status_code == 201
    camera_id = created_camera.json()["id"]
    activated_camera = client.post(f"/api/v1/cameras/{camera_id}/activate")
    assert activated_camera.status_code == 200
    created_person = client.post(
        "/api/v1/persons",
        json={"full_name": "Integration Student", "student_id": "I" + suffix[:12]},
    )
    assert created_person.status_code == 201
    person_id = created_person.json()["id"]
    teacher = client.post(
        "/api/v1/admin/accounts",
        json={"email": teacher_email, "password": "Teacher password 9!", "role": "TEACHER"},
    )
    student = client.post(
        "/api/v1/admin/accounts",
        json={
            "email": student_email,
            "password": "Student password 9!",
            "role": "STUDENT",
            "person_id": person_id,
        },
    )
    assert teacher.status_code == student.status_code == 201
    classroom = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Integration class", "course": "TEST", "section": "01", "term": "2026"},
    )
    assert classroom.status_code == 201
    classroom_id = classroom.json()["id"]
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/teachers",
            json={"teacher_id": teacher.json()["id"]},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/members", json={"person_id": person_id}
        ).status_code
        == 201
    )

    student_client = TestClient(client.app)
    sign_in(student_client, student_email, "Student password 9!")
    assert student_client.get("/api/v1/me/profile").json()["id"] == person_id
    image_buffer = BytesIO()
    Image.new("RGB", (64, 64), "orange").save(image_buffer, format="JPEG")
    uploaded = student_client.post(
        "/api/v1/me/face-images",
        files={"files": ("registration.jpg", image_buffer.getvalue(), "image/jpeg")},
    )
    assert uploaded.status_code == 201
    image_result = uploaded.json()["results"][0]
    assert image_result["status"] == "STORED_PENDING"
    image_id = image_result["image"]["id"]
    assert image_result["image"]["review_state"] == "PENDING_REVIEW"

    teacher_client = TestClient(client.app)
    sign_in(teacher_client, teacher_email, "Teacher password 9!")
    pending = teacher_client.get("/api/v1/teacher/face-images/pending")
    assert pending.status_code == 200
    assert any(item["id"] == image_id for item in pending.json()["items"])
    review = teacher_client.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "APPROVE"}
    )
    assert review.status_code == 200
    assert review.json()["review_state"] == "APPROVED"
    face_key = client.app.state.face_registry.repository.get_image(image_id).storage_key
    object_keys.append((stores["faces"], face_key))

    local_now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    session = teacher_client.post(
        f"/api/v1/teacher/classes/{classroom_id}/sessions",
        json={
            "camera_id": camera_id,
            "label": "Integration attendance",
            "starts_at": (local_now - timedelta(minutes=5)).isoformat(),
            "ends_at": (local_now + timedelta(minutes=20)).isoformat(),
            "timezone_id": "Asia/Ho_Chi_Minh",
        },
    )
    assert session.status_code == 201
    session_id = session.json()["id"]
    event = client.app.state.recognition_events.record_verified(
        camera_id=camera_id,
        camera_name="Integration camera",
        session_id=session_id,
        track_id=1,
        person_id=person_id,
        full_name="Integration Student",
        student_id="I" + suffix[:12],
        evidence=b"synthetic-test-evidence",
        retention_days=30,
        attendance_enabled=True,
    )
    assert event is not None
    object_keys.append((stores["evidence"], str(event["evidence_key"])))

    report = teacher_client.get(
        f"/api/v1/teacher/classes/{classroom_id}/sessions/{session_id}/attendance"
    )
    assert report.status_code == 200
    assert report.json()["items"][0]["status"] == "PRESENT"
    assert "evidence_key" not in report.text
    assert student_client.get("/api/v1/me/attendance").json()["items"]
    assert student_client.get("/api/v1/me/events").json()["items"]
    assert student_client.get("/api/v1/admin/accounts").status_code == 403
    assert (
        student_client.get("/api/v1/me/profile?person_id=another-person").json()["id"] == person_id
    )

    student_client.close()
    teacher_client.close()
