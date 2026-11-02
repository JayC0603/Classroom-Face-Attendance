import pytest
from control_api.auth_domain import AuthService, InMemoryAuthRepository
from control_api.main import create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def make_client():
    auth = AuthService(InMemoryAuthRepository())
    auth.bootstrap_admin("admin@example.test", "Admin password 9!")
    return TestClient(create_app(auth_service=auth)), auth


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": "admin@example.test", "password": "Admin password 9!"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_auth_lifecycle_csrf_and_origin_are_enforced() -> None:
    client, _ = make_client()
    login_response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": "admin@example.test", "password": "Admin password 9!"},
    )
    csrf = login_response.json()["csrf_token"]
    cookie = login_response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200
    csrf = current.json()["csrf_token"]

    missing = client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
    bad_origin = client.post(
        "/api/v1/auth/logout", headers={"Origin": "https://attacker.test", "X-CSRF-Token": csrf}
    )
    assert missing.status_code == 403
    assert bad_origin.status_code == 403
    ok = client.post(
        "/api/v1/auth/logout", headers={"Origin": "http://testserver", "X-CSRF-Token": csrf}
    )
    assert ok.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_change_rotates_session_and_csrf_secret() -> None:
    client, _ = make_client()
    old_csrf = login(client)
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": "http://testserver", "X-CSRF-Token": old_csrf},
        json={"old_password": "Admin password 9!", "new_password": "Updated admin password 10!"},
    )
    assert response.status_code == 200
    new_csrf = response.json()["csrf_token"]
    assert new_csrf != old_csrf
    assert client.get("/api/v1/auth/me").status_code == 200
    stale_csrf = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": old_csrf},
    )
    assert stale_csrf.status_code == 403


def test_sensitive_route_families_require_session_and_admin_role() -> None:
    unauthenticated = TestClient(create_app(auth_service=AuthService(InMemoryAuthRepository())))
    for path in (
        "/api/v1/cameras",
        "/api/v1/persons",
        "/api/v1/person-models",
        "/api/v1/models",
        "/api/v1/vector-index/status",
        "/api/v1/attendance",
        "/api/v1/recognition/events",
        "/api/v1/runtime/status",
    ):
        response = unauthenticated.get(path)
        assert response.status_code == 401, path


def test_admin_can_create_accounts_but_cannot_link_person_to_teacher() -> None:
    client, _ = make_client()
    csrf = login(client)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/admin/accounts",
        headers=headers,
        json={
            "email": "student@example.test",
            "password": "Student password 9!",
            "role": "STUDENT",
            "person_id": "person-7",
        },
    )
    assert created.status_code == 201
    invalid = client.post(
        "/api/v1/admin/accounts",
        headers=headers,
        json={
            "email": "teacher@example.test",
            "password": "Teacher password 9!",
            "role": "TEACHER",
            "person_id": "person-7",
        },
    )
    assert invalid.status_code == 422


def test_browser_websocket_uses_session_cookie_worker_header_is_not_browser_identity() -> None:
    client, _ = make_client()
    login(client)
    with client.websocket_connect(
        "/api/v1/ws", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "system.ready"
    unauthenticated = TestClient(create_app(auth_service=AuthService(InMemoryAuthRepository())))
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with unauthenticated.websocket_connect(
            "/api/v1/ws",
            headers={"Origin": "http://testserver", "X-Worker-Token": "worker-test-token"},
        ) as websocket:
            websocket.receive_json()
    assert disconnected.value.code == 1008


def test_open_websocket_closes_after_logout_revokes_session(monkeypatch) -> None:
    monkeypatch.setattr("control_api.main.WS_SESSION_RECHECK_SECONDS", 0.05)
    client, _ = make_client()
    csrf = login(client)
    with client.websocket_connect(
        "/api/v1/ws", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "system.ready"
        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert logged_out.status_code == 204
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_json()
        assert disconnected.value.code == 1008


def test_login_requires_same_origin_and_role_denial_is_server_authoritative() -> None:
    client, auth = make_client()
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.test"},
        json={"email": "admin@example.test", "password": "Admin password 9!"},
    )
    assert response.status_code == 403
    auth.create_account("teacher@example.test", "Teacher password 9!", "TEACHER")
    student = auth.create_account(
        "student@example.test", "Student password 9!", "STUDENT", "person-3"
    )
    assert student.person_id == "person-3"
    teacher_login = auth.login("teacher@example.test", "Teacher password 9!", "127.0.0.1")
    client.cookies.set("fr_session", teacher_login.session_token, path="/api/v1")
    assert client.get("/api/v1/cameras").status_code == 403


def test_anonymous_request_is_rejected_on_every_registered_browser_api_path() -> None:
    import re

    app = create_app(auth_service=AuthService(InMemoryAuthRepository()))
    client = TestClient(app)
    paths = {
        path
        for path in app.openapi()["paths"]
        if path.startswith("/api/v1/")
        and path != "/api/v1/auth/login"
        and not path.startswith("/api/v1/internal/")
    }
    assert paths
    for template in sorted(paths):
        path = re.sub(r"\{[^{}]+\}", "test-value", template)
        assert client.get(path).status_code == 401, path
