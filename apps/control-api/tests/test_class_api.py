from control_api.auth_domain import AuthService, InMemoryAuthRepository
from control_api.cameras import (
    CameraCreate,
    CameraService,
    FernetCameraSecretProtector,
    InMemoryCameraRepository,
)
from control_api.class_domain import InMemoryClassRepository
from control_api.main import create_app
from fastapi.testclient import TestClient


def make_client():
    auth_repo = InMemoryAuthRepository()
    auth = AuthService(auth_repo)
    auth.bootstrap_admin("admin@example.test", "Admin password 9!")
    auth.create_account("teacher@example.test", "Teacher password 9!", "TEACHER")
    auth.create_account("student@example.test", "Student password 9!", "STUDENT", "person-1")
    classes = InMemoryClassRepository()
    classes.seed_teacher(auth_repo.user_by_email("teacher@example.test").id)
    classes.seed_student_person("person-1", "An Nguyen", "SV001")
    classes.seed_student_person("person-2", "Binh Tran", "SV002")
    camera_service = CameraService(
        InMemoryCameraRepository(), FernetCameraSecretProtector.generate()
    )
    camera = camera_service.create(
        CameraCreate(name="Cam", source_type="local_webcam", device_index=0)
    )
    camera_service.activate(camera.id)

    class EmptyEvents:
        def verified_events(self, **_kwargs):
            return []

    app = create_app(
        auth_service=auth,
        camera_service=camera_service,
        class_repository=classes,
        class_event_query=EmptyEvents(),
    )
    return TestClient(app), auth_repo, classes, camera.id


def as_role(client, auth_repo, email, password):
    auth_service = client.app.state.auth_service
    user = auth_repo.user_by_email(email)
    result = auth_service.login(user.email, password, "127.0.0.1")
    client.cookies.set("fr_session", result.session_token, path="/api/v1")
    client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": result.csrf_token})


def test_admin_creates_class_assigns_teacher_and_enrolls_student_then_teacher_reads_assignment():
    client, auth_repo, _classes, camera_id = make_client()
    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    created = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Python", "course": "PRF", "section": "SE1801", "term": "Fall 2026"},
    )
    assert created.status_code == 201
    classroom_id = created.json()["id"]
    teacher_id = auth_repo.user_by_email("teacher@example.test").id
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/teachers", json={"teacher_id": teacher_id}
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/members", json={"person_id": "person-1"}
        ).status_code
        == 201
    )

    as_role(client, auth_repo, "teacher@example.test", "Teacher password 9!")
    classes = client.get("/api/v1/teacher/classes")
    assert [item["id"] for item in classes.json()["items"]] == [classroom_id]
    available_cameras = client.get("/api/v1/teacher/cameras")
    assert available_cameras.status_code == 200
    assert available_cameras.json()["items"] == [
        {"id": camera_id, "name": "Cam", "source_type": "local_webcam"}
    ]
    assert "rtsp_url" not in available_cameras.text
    assert "device_index" not in available_cameras.text
    other = client.get("/api/v1/teacher/classes/not-assigned/sessions")
    assert other.status_code == 404
    assert camera_id


def test_admin_rejects_non_teacher_and_unlinked_person_assignments():
    client, auth_repo, _classes, _camera_id = make_client()
    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    classroom_id = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Python", "course": "PRF", "section": "SE1801", "term": "Fall 2026"},
    ).json()["id"]
    student_id = auth_repo.user_by_email("student@example.test").id
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/teachers", json={"teacher_id": student_id}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/admin/classrooms/{classroom_id}/members", json={"person_id": "unknown"}
        ).status_code
        == 422
    )


def test_teacher_creates_session_and_gets_roster_report_without_evidence_keys():
    client, auth_repo, _classes, camera_id = make_client()
    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    classroom_id = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Python", "course": "PRF", "section": "SE1801", "term": "Fall 2026"},
    ).json()["id"]
    teacher_id = auth_repo.user_by_email("teacher@example.test").id
    client.post(
        f"/api/v1/admin/classrooms/{classroom_id}/teachers", json={"teacher_id": teacher_id}
    )
    client.post(f"/api/v1/admin/classrooms/{classroom_id}/members", json={"person_id": "person-1"})

    as_role(client, auth_repo, "teacher@example.test", "Teacher password 9!")
    created = client.post(
        f"/api/v1/teacher/classes/{classroom_id}/sessions",
        json={
            "camera_id": camera_id,
            "label": "Morning",
            "starts_at": "2026-10-20T09:00:00+07:00",
            "ends_at": "2026-10-20T10:00:00+07:00",
            "timezone_id": "Asia/Ho_Chi_Minh",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    completed = client.post(
        f"/api/v1/teacher/classes/{classroom_id}/sessions/{session_id}/complete"
    )
    assert completed.status_code == 200
    assert completed.json()["done"] is True
    report = client.get(f"/api/v1/teacher/classes/{classroom_id}/sessions/{session_id}/attendance")
    assert report.status_code == 200
    assert report.json()["items"][0]["status"] == "UNKNOWN_SYSTEM"
    assert "evidence_key" not in report.text


def test_session_api_rejects_naive_or_invalid_zone_time():
    client, auth_repo, _classes, camera_id = make_client()
    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    classroom_id = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Python", "course": "PRF", "section": "SE1801", "term": "Fall 2026"},
    ).json()["id"]
    teacher_id = auth_repo.user_by_email("teacher@example.test").id
    client.post(
        f"/api/v1/admin/classrooms/{classroom_id}/teachers", json={"teacher_id": teacher_id}
    )
    as_role(client, auth_repo, "teacher@example.test", "Teacher password 9!")
    response = client.post(
        f"/api/v1/teacher/classes/{classroom_id}/sessions",
        json={
            "camera_id": camera_id,
            "label": "Morning",
            "starts_at": "2026-10-20T09:00:00",
            "ends_at": "2026-10-20T10:00:00",
            "timezone_id": "Asia/Ho_Chi_Minh",
        },
    )
    assert response.status_code == 422


def test_admin_can_update_disable_and_remove_class_assignments_while_teacher_scope_is_enforced():
    client, auth_repo, _classes, _camera_id = make_client()
    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    created = client.post(
        "/api/v1/admin/classrooms",
        json={"name": "Python", "course": "PRF", "section": "SE1801", "term": "Fall 2026"},
    )
    classroom_id = created.json()["id"]
    teacher_id = auth_repo.user_by_email("teacher@example.test").id
    client.post(
        f"/api/v1/admin/classrooms/{classroom_id}/teachers", json={"teacher_id": teacher_id}
    )
    updated = client.patch(
        f"/api/v1/admin/classrooms/{classroom_id}", json={"name": "Python nâng cao"}
    )
    assert updated.status_code == 200
    assert (
        client.get(f"/api/v1/admin/classrooms/{classroom_id}").json()["name"] == "Python nâng cao"
    )
    assert (
        client.delete(f"/api/v1/admin/classrooms/{classroom_id}/teachers/{teacher_id}").status_code
        == 204
    )

    as_role(client, auth_repo, "teacher@example.test", "Teacher password 9!")
    assert client.get("/api/v1/teacher/classes").json()["items"] == []
    assert (
        client.post(
            "/api/v1/admin/classrooms",
            json={"name": "Forbidden", "course": "PRF", "section": "SE1801", "term": "Fall"},
        ).status_code
        == 403
    )

    as_role(client, auth_repo, "admin@example.test", "Admin password 9!")
    assert client.delete(f"/api/v1/admin/classrooms/{classroom_id}").status_code == 204
    assert client.get(f"/api/v1/admin/classrooms/{classroom_id}").status_code == 200
    assert client.get("/api/v1/admin/classrooms").json()["items"][0]["active"] is False
