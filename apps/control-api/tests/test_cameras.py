from auth_test_support import TestClient, create_app
from control_api.cameras import (
    CameraConnectionResult,
    CameraService,
    FernetCameraSecretProtector,
    InMemoryCameraRepository,
)
from cryptography.fernet import Fernet
from fr_contracts.runtime_status import CameraConnectionState, RuntimeStatus


class ConnectedTester:
    def test(self, rtsp_url: str) -> CameraConnectionResult:
        assert rtsp_url == "rtsp://alice:secret@camera.local/stream"
        return CameraConnectionResult(status="CONNECTED")


def client() -> TestClient:
    service = CameraService(
        repository=InMemoryCameraRepository(),
        protector=FernetCameraSecretProtector(Fernet.generate_key()),
        connection_tester=ConnectedTester(),
    )
    return TestClient(create_app(camera_service=service, internal_camera_token="worker-test-token"))


def test_stale_camera_edit_returns_conflict_without_overwriting_newer_configuration():
    api = client()
    camera = api.post("/api/v1/cameras", json={"name": "Door", "source_type": "webcam"}).json()
    path = f"/api/v1/cameras/{camera['id']}"
    assert (
        api.patch(
            path, json={"name": "New name", "expected_revision": camera["revision"]}
        ).status_code
        == 200
    )
    stale = api.patch(path, json={"name": "Stale name", "expected_revision": camera["revision"]})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "CAMERA_REVISION_CONFLICT"
    assert api.get(path).json()["name"] == "New name"


def test_delete_camera_checks_revision_and_removes_active_worker_configuration():
    api = client()
    camera = api.post(
        "/api/v1/cameras", json={"name": "Door", "source_type": "local_webcam"}
    ).json()
    path = f"/api/v1/cameras/{camera['id']}"
    active = api.post(f"{path}/activate").json()
    assert api.delete(path, params={"expected_revision": camera["revision"]}).status_code == 409
    assert api.get(path).status_code == 200
    assert api.delete(path, params={"expected_revision": active["revision"]}).status_code == 204
    assert api.get(path).status_code == 404
    assert api.get("/api/v1/cameras").json()["items"] == []
    assert (
        api.get(
            "/api/v1/internal/cameras/active", headers={"X-Worker-Token": "worker-test-token"}
        ).json()["items"]
        == []
    )


def test_camera_create_redacts_secret_and_activation_delivers_internal_config() -> None:
    api = client()

    created = api.post(
        "/api/v1/cameras",
        json={
            "name": "Entrance",
            "rtsp_url": "rtsp://alice:secret@camera.local/stream",
            "codec": "AUTO",
            "enabled": True,
            "sampling_fps": 5.0,
        },
    )

    assert created.status_code == 201
    public = created.json()
    assert public["rtsp_url"] == "rtsp://***:***@camera.local/stream"
    assert "secret" not in created.text
    assert public["lifecycle"] == "DRAFT"

    tested = api.post(f"/api/v1/cameras/{public['id']}/test-connection")
    assert tested.json() == {"status": "CONNECTED", "error_category": None}

    activated = api.post(f"/api/v1/cameras/{public['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["lifecycle"] == "ACTIVE"

    listed = api.get("/api/v1/cameras")
    assert "secret" not in listed.text
    assert listed.json()["items"][0]["revision"] == activated.json()["revision"]

    assert api.get("/api/v1/internal/cameras/active").status_code == 403
    internal = api.get(
        "/api/v1/internal/cameras/active", headers={"X-Worker-Token": "worker-test-token"}
    )
    assert internal.status_code == 200
    assert internal.json()["items"][0]["rtsp_url"] == ("rtsp://alice:secret@camera.local/stream")


def test_camera_edit_keeps_existing_secret_until_replacement_is_supplied() -> None:
    api = client()
    created = api.post(
        "/api/v1/cameras",
        json={"name": "Entrance", "rtsp_url": "rtsp://alice:secret@camera.local/stream"},
    ).json()

    updated = api.patch(f"/api/v1/cameras/{created['id']}", json={"name": "Lobby"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Lobby"
    assert "secret" not in updated.text

    internal = api.post(f"/api/v1/cameras/{created['id']}/activate")
    assert internal.status_code == 200
    active = api.get(
        "/api/v1/internal/cameras/active", headers={"X-Worker-Token": "worker-test-token"}
    ).json()["items"][0]
    assert active["rtsp_url"] == "rtsp://alice:secret@camera.local/stream"


def test_camera_detail_and_runtime_are_scoped_and_secret_safe() -> None:
    api = client()
    created = api.post(
        "/api/v1/cameras",
        json={"name": "Entrance", "rtsp_url": "rtsp://alice:secret@camera.local/live"},
    ).json()
    detail = api.get(f"/api/v1/cameras/{created['id']}")
    assert detail.status_code == 200
    assert "secret" not in detail.text

    status = RuntimeStatus(
        worker_id="worker-1",
        camera_id=created["id"],
        camera_state=CameraConnectionState.RECONNECTING,
        input_fps=0,
        processed_fps=0,
        dropped_frames=2,
        active_tracks=0,
        ring_buffer_frames=0,
        ring_buffer_capacity=10,
        worker_health="degraded",
        active_config_revision=created["revision"],
        stream_session_id="session-safe-id",
        reconnect_count=2,
        error_category="UNREACHABLE",
    )
    assert (
        api.put(
            "/api/v1/runtime/status",
            json=status.model_dump(mode="json"),
            headers={"X-Worker-Token": "worker-test-token"},
        ).status_code
        == 200
    )
    runtime = api.get(f"/api/v1/cameras/{created['id']}/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["items"][0]["error_category"] == "UNREACHABLE"
    assert "secret" not in runtime.text

    assert api.get("/api/v1/cameras/missing").status_code == 404
    assert api.get("/api/v1/cameras/missing/runtime").status_code == 404


def test_invalid_camera_url_validation_never_echoes_credentials() -> None:
    api = client()
    response = api.post(
        "/api/v1/cameras",
        json={
            "name": "Entrance",
            "rtsp_url": "http://alice:supersecret@camera.local/live",
        },
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "request validation failed",
        "error_category": "VALIDATION_ERROR",
    }
    assert "alice" not in response.text
    assert "supersecret" not in response.text


def test_public_camera_url_removes_query_credentials() -> None:
    api = client()
    response = api.post(
        "/api/v1/cameras",
        json={
            "name": "Token camera",
            "rtsp_url": "rtsp://camera.local/live?access_token=query-secret",
        },
    )
    assert response.status_code == 201
    assert response.json()["rtsp_url"] == "rtsp://camera.local/live"
    assert "query-secret" not in response.text
