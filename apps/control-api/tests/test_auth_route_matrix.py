import pytest
from control_api.auth_domain import AuthService, InMemoryAuthRepository
from control_api.main import create_app
from fastapi.testclient import TestClient
from fr_contracts.runtime_status import CameraConnectionState, RuntimeStatus
from starlette.websockets import WebSocketDisconnect

WORKER_TOKEN = "worker-test-token"


def status_payload() -> dict:
    return RuntimeStatus(
        worker_id="worker-01",
        camera_id="camera-01",
        camera_state=CameraConnectionState.CONNECTED,
        input_fps=25.0,
        processed_fps=10.0,
        dropped_frames=15,
        active_tracks=2,
        ring_buffer_frames=50,
        ring_buffer_capacity=100,
        worker_health="healthy",
        active_config_revision="revision-7",
    ).model_dump(mode="json")


def test_runtime_status_write_requires_configured_worker_token() -> None:
    client = TestClient(create_app(internal_camera_token=WORKER_TOKEN))

    missing = client.put("/api/v1/runtime/status", json=status_payload())
    mismatched = client.put(
        "/api/v1/runtime/status",
        json=status_payload(),
        headers={"X-Worker-Token": "wrong-token"},
    )
    authorized = client.put(
        "/api/v1/runtime/status",
        json=status_payload(),
        headers={"X-Worker-Token": WORKER_TOKEN},
    )

    assert missing.status_code == 403
    assert mismatched.status_code == 403
    assert authorized.status_code == 200


def test_runtime_status_write_fails_closed_when_worker_token_is_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("FR_WORKER_INTERNAL_TOKEN", raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/v1/runtime/status",
        json=status_payload(),
        headers={"X-Worker-Token": "any-token"},
    )

    assert response.status_code == 403


def test_runtime_websocket_requires_browser_session_and_same_origin() -> None:
    auth = AuthService(InMemoryAuthRepository())
    auth.bootstrap_admin("admin@example.test", "Admin password 9!")
    auth.create_account("teacher@example.test", "Teacher password 9!", "TEACHER")
    client = TestClient(create_app(internal_camera_token=WORKER_TOKEN, auth_service=auth))
    login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": "admin@example.test", "password": "Admin password 9!"},
    )
    assert login.status_code == 200

    with client.websocket_connect(
        "/api/v1/ws", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "system.ready"

    token = client.cookies.get("fr_session")
    assert token
    teacher_client = TestClient(create_app(auth_service=auth))
    teacher_login = teacher_client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": "teacher@example.test", "password": "Teacher password 9!"},
    )
    assert teacher_login.status_code == 200
    teacher_token = teacher_client.cookies.get("fr_session")
    assert teacher_token
    cases = (
        (
            TestClient(create_app(auth_service=AuthService(InMemoryAuthRepository()))),
            {"Origin": "http://testserver"},
        ),
        (
            TestClient(create_app(auth_service=auth)),
            {"Origin": "https://attacker.test", "Cookie": f"fr_session={token}"},
        ),
        (
            TestClient(create_app(auth_service=AuthService(InMemoryAuthRepository()))),
            {"Origin": "http://testserver", "X-Worker-Token": WORKER_TOKEN},
        ),
        (TestClient(create_app(auth_service=auth)), {"Cookie": f"fr_session={token}"}),
        (
            TestClient(create_app(auth_service=auth)),
            {"Origin": "http://testserver", "Cookie": f"fr_session={teacher_token}"},
        ),
    )
    for rejected_client, headers in cases:
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with rejected_client.websocket_connect("/api/v1/ws", headers=headers):
                pass
        assert disconnected.value.code == 1008


def test_runtime_websocket_fails_closed_when_worker_token_is_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("FR_WORKER_INTERNAL_TOKEN", raising=False)
    client = TestClient(create_app())

    with pytest.raises(WebSocketDisconnect) as disconnected:
        with client.websocket_connect("/api/v1/ws"):
            pass
    assert disconnected.value.code == 1008


def test_health_remains_public_without_worker_token(monkeypatch) -> None:
    monkeypatch.delenv("FR_WORKER_INTERNAL_TOKEN", raising=False)

    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200


def test_every_registered_http_route_has_a_browser_or_worker_boundary() -> None:
    import re

    client = TestClient(create_app(auth_service=AuthService(InMemoryAuthRepository())))
    paths = client.app.openapi()["paths"]
    required_task4_routes = {
        "/api/v1/me/profile",
        "/api/v1/me/face-images",
        "/api/v1/me/face-images/{image_id}/content",
        "/api/v1/me/attendance",
        "/api/v1/me/events",
        "/api/v1/me/events/{event_id}/evidence",
        "/api/v1/admin/face-images/pending",
        "/api/v1/admin/face-images/{image_id}/review",
        "/api/v1/teacher/face-images/pending",
        "/api/v1/teacher/face-images/{image_id}/content",
        "/api/v1/teacher/face-images/{image_id}/review",
        "/api/v1/teacher/cameras",
    }
    assert required_task4_routes <= paths.keys()
    checked = 0

    for path, operations in paths.items():
        if not path.startswith("/api/v1/"):
            continue
        concrete_path = re.sub(r"\{[^}]+\}", "test-resource", path)
        for method in operations:
            if method.lower() == "post" and path == "/api/v1/auth/login":
                continue  # Public entry point; separately constrained by Origin.
            headers = {"Content-Type": "application/json"}
            body = None
            params = None
            if path.startswith("/api/v1/internal/"):
                expected_status = 403  # Dedicated worker-token routes.
                if path.endswith("/activation-result"):
                    body = {"success": False}
                if path.endswith("/webcam/frame"):
                    params = {"session_id": "test-session"}
                elif path.endswith("/preview"):
                    params = {"revision": "test-revision"}
            elif method.lower() == "put" and path == "/api/v1/runtime/status":
                expected_status = 403
                body = status_payload()
                headers["X-Worker-Token"] = "missing-config-must-fail-closed"
            else:
                expected_status = 401  # Browser routes require a session before validation.

            response = client.request(
                method.upper(), concrete_path, params=params, json=body, headers=headers
            )
            assert response.status_code == expected_status, f"{method.upper()} {path}"
            checked += 1

    assert checked >= 20
