from __future__ import annotations

from io import BytesIO

import numpy as np
from control_api.auth_domain import AuthService, InMemoryAuthRepository
from control_api.class_domain import CameraSummary, ClassService, InMemoryClassRepository
from control_api.face_image_storage import FaceImageStorage
from control_api.face_registry import (
    FaceRegistryService,
    InMemoryFaceRegistryRepository,
    PersonCreate,
)
from control_api.main import create_app
from control_api.simple_face_recognition import FaceSample, RegisteredFaceComparison
from fastapi.testclient import TestClient
from PIL import Image


class Models:
    def load(self) -> None:
        return None

    def status(self) -> dict[str, str]:
        return {"state": "READY"}

    def analyze(self, payload: bytes) -> list[FaceSample]:
        assert payload
        return [FaceSample([0.0, 0.0, 1.0, 1.0], np.array([1.0, 0.0]))]


class NoFaceModels(Models):
    def analyze(self, payload: bytes) -> list[FaceSample]:
        assert payload
        return []


class UnusedCameraLookup:
    def require(self, camera_id: str) -> object:
        return camera_id

    def active_list(self) -> list[CameraSummary]:
        return []


def jpeg() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (48, 48), "blue").save(stream, format="JPEG")
    return stream.getvalue()


def setup(tmp_path):
    auth_repo = InMemoryAuthRepository()
    auth = AuthService(auth_repo)
    auth.bootstrap_admin("admin@example.test", "Admin password 9!")
    person_repo = InMemoryFaceRegistryRepository()
    registry = FaceRegistryService(person_repo, FaceImageStorage(tmp_path / "faces"))
    person = registry.create_person(PersonCreate(full_name="Student One", student_id="SV-1"))
    student = auth.create_account(
        "student@example.test", "Student password 9!", "STUDENT", person.id
    )
    teacher = auth.create_account("teacher@example.test", "Teacher password 9!", "TEACHER")
    classes = InMemoryClassRepository()
    classes.seed_teacher(teacher.id)
    classes.seed_student_person(person.id, person.full_name, person.student_id)
    class_service = ClassService(classes, UnusedCameraLookup(), object())
    classroom = class_service.create_classroom("A", "C", "1", "2026")
    class_service.add_teacher(classroom.id, teacher.id)
    class_service.enroll_person(classroom.id, person.id)
    comparison = RegisteredFaceComparison(registry, Models())
    app = create_app(
        auth_service=auth,
        class_repository=classes,
        face_registry_service=registry,
        face_comparison_service=comparison,
    )
    return TestClient(app), auth, registry, comparison, person, student, teacher, classes, classroom


def login(client: TestClient, auth: AuthService, email: str, password: str) -> None:
    result = auth.login(email, password, "127.0.0.1")
    client.cookies.set("fr_session", result.session_token, path="/api/v1")
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": result.csrf_token}
    )


def test_student_self_service_uses_principal_person_and_upload_stays_pending(tmp_path):
    client, auth, registry, comparison, person, student, *_ = setup(tmp_path)
    login(client, auth, student.email, "Student password 9!")

    profile = client.get("/api/v1/me/profile")
    assert profile.status_code == 200
    assert profile.json()["id"] == person.id
    assert profile.json()["recognition_ready"] is False
    assert client.get("/api/v1/me/profile?person_id=someone-else").json()["id"] == person.id
    uploaded = client.post(
        "/api/v1/me/face-images",
        files={"files": ("enrollment.jpg", jpeg(), "image/jpeg")},
        headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]},
    )
    assert uploaded.status_code == 201
    sample = uploaded.json()["results"][0]["image"]
    assert sample["review_state"] == "PENDING_REVIEW"
    assert "storage_key" not in sample and "evidence_ref" not in sample
    assert comparison.status(force=True)["reference_images"] == 0
    assert client.get(f"/api/v1/me/face-images/{sample['id']}/content").status_code == 200
    assert registry.repository.list_images(person.id)[0].storage_key

    other = registry.create_person(PersonCreate(full_name="Other Student", student_id="SV-2"))
    other_image = registry.save_image(other.id, "other.jpg", jpeg())
    assert client.get(f"/api/v1/me/face-images/{other_image.id}/content").status_code == 404

    admin = TestClient(client.app)
    login(admin, auth, "admin@example.test", "Admin password 9!")
    approved = admin.post(
        f"/api/v1/admin/face-images/{sample['id']}/review", json={"decision": "APPROVE"}
    )
    assert approved.status_code == 200
    refreshed = client.get("/api/v1/me/profile")
    assert refreshed.json()["recognition_ready"] is True


def test_student_readiness_requires_an_approved_usable_face(tmp_path):
    client, auth, registry, comparison, person, student, *_ = setup(tmp_path)
    comparison.models = NoFaceModels()
    image = registry.save_image(person.id, "empty-face.jpg", jpeg())
    registry.review_image(image.id, "reviewer-1", "APPROVE")
    login(client, auth, student.email, "Student password 9!")

    profile = client.get("/api/v1/me/profile")

    assert profile.status_code == 200
    assert profile.json()["recognition_ready"] is False


def test_student_event_evidence_is_scoped_to_own_person(tmp_path, monkeypatch):
    monkeypatch.setenv("FR_EVENT_STORE_ROOT", str(tmp_path / "events"))
    client, auth, _registry, _comparison, person, student, *_ = setup(tmp_path)
    store = client.app.state.recognition_events
    own = store.record_verified(
        camera_id="camera-1", camera_name="Cam 1", session_id="session-1", track_id=1,
        person_id=person.id, full_name="Self", student_id="SV-1",
        evidence=b"self-evidence", retention_days=30, attendance_enabled=True,
    )
    foreign = store.record_verified(
        camera_id="camera-1", camera_name="Cam 1", session_id="session-2", track_id=2,
        person_id="person-other", full_name="Other", student_id="SV-OTHER",
        evidence=b"foreign-evidence", retention_days=30, attendance_enabled=True,
    )
    login(client, auth, student.email, "Student password 9!")

    allowed = client.get(f"/api/v1/me/events/{own['id']}/evidence")
    denied = client.get(f"/api/v1/me/events/{foreign['id']}/evidence")
    assert allowed.status_code == 200
    assert allowed.content == b"self-evidence"
    assert allowed.headers["cache-control"] == "private, no-store"
    assert denied.status_code == 404


def test_student_without_existing_linked_person_gets_conflict(tmp_path):
    client, auth, *_ = setup(tmp_path)
    unlinked = auth.create_account(
        "unlinked@example.test", "Student password 9!", "STUDENT", "stale-person-id"
    )
    login(client, auth, unlinked.email, "Student password 9!")
    response = client.get("/api/v1/me/profile")
    assert response.status_code == 409
    assert response.json()["detail"] == "STUDENT_PERSON_NOT_LINKED"


def test_teacher_review_requires_current_assignment_and_membership_and_audits_privately(
    tmp_path,
):
    client, auth, _registry, comparison, person, _student, teacher, classes, classroom = setup(
        tmp_path
    )
    student_client = TestClient(client.app)
    login(student_client, auth, "student@example.test", "Student password 9!")
    uploaded = student_client.post(
        "/api/v1/me/face-images",
        files={"files": ("enrollment.jpg", jpeg(), "image/jpeg")},
    )
    assert uploaded.status_code == 201
    image_id = uploaded.json()["results"][0]["image"]["id"]

    unrelated = auth.create_account("unrelated@example.test", "Teacher password 9!", "TEACHER")
    classes.seed_teacher(unrelated.id)
    reviewer = TestClient(client.app)
    login(reviewer, auth, unrelated.email, "Teacher password 9!")
    denied = reviewer.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "APPROVE"}
    )
    assert denied.status_code in {403, 404}

    classes.remove_teacher(classroom.id, teacher.id)
    login(reviewer, auth, teacher.email, "Teacher password 9!")
    unassigned = reviewer.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "APPROVE"}
    )
    assert unassigned.status_code in {403, 404}

    classes.add_teacher(classroom.id, teacher.id)
    classes.remove_member(classroom.id, person.id)
    login(reviewer, auth, teacher.email, "Teacher password 9!")
    nonmember = reviewer.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "APPROVE"}
    )
    assert nonmember.status_code in {403, 404}

    classes.add_member(classroom.id, person.id)
    login(reviewer, auth, teacher.email, "Teacher password 9!")
    approved = reviewer.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "APPROVE"}
    )
    assert approved.status_code == 200
    assert approved.json()["review_state"] == "APPROVED"
    assert approved.json()["content_url"] == f"/api/v1/teacher/face-images/{image_id}/content"
    assert comparison.status(force=True)["reference_images"] == 1
    assert reviewer.get(f"/api/v1/teacher/face-images/{image_id}/content").status_code == 404
    duplicate = reviewer.post(
        f"/api/v1/teacher/face-images/{image_id}/review", json={"decision": "REJECT"}
    )
    assert duplicate.status_code == 409


def test_admin_can_review_any_pending_image_without_exposing_storage_or_biometric_data(tmp_path):
    client, auth, registry, _comparison, person, _student, *_ = setup(tmp_path)
    image = registry.save_image(person.id, "admin.jpg", jpeg())
    login(client, auth, "admin@example.test", "Admin password 9!")
    pending = client.get("/api/v1/admin/face-images/pending")
    assert pending.status_code == 200
    assert pending.json()["items"][0]["person_name"] == person.full_name
    assert pending.json()["items"][0]["student_id"] == person.student_id
    assert pending.json()["items"][0]["person_number"] == person.person_number
    assert "storage_key" not in pending.text and "embedding" not in pending.text
    response = client.post(
        f"/api/v1/admin/face-images/{image.id}/review", json={"decision": "REJECT"}
    )
    assert response.status_code == 200
    assert response.json()["review_state"] == "REJECTED"
    assert "storage_key" not in response.text and "embedding" not in response.text
    audit = registry.review_audit()
    assert len(audit) == 1
    assert audit[0]["reviewer_id"]
    assert audit[0]["person_id"] == person.id
    assert audit[0]["image_id"] == image.id
    assert set(audit[0]) == {"reviewer_id", "person_id", "image_id", "decision", "reviewed_at"}
