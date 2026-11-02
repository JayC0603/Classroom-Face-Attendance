from auth_test_support import TestClient, create_app
from control_api.cameras import CameraService, FernetCameraSecretProtector, InMemoryCameraRepository


def test_local_webcam_create_edit_and_delivery_without_browser_session():
    client = TestClient(
        create_app(
            camera_service=CameraService(
                InMemoryCameraRepository(),
                FernetCameraSecretProtector.generate(),
            ),
            internal_camera_token="worker",
        )
    )
    created = client.post(
        "/api/v1/cameras",
        json={
            "name": "Worker webcam",
            "source_type": "local_webcam",
            "device_index": 1,
        },
    )
    assert created.status_code == 201
    camera = created.json()
    assert camera["source_type"] == "local_webcam" and camera["device_index"] == 1
    assert camera["rtsp_url"] is None
    camera_id = camera["id"]
    client.post(f"/api/v1/cameras/{camera_id}/activate")
    active = client.get("/api/v1/internal/cameras/active", headers={"X-Worker-Token": "worker"})
    assert active.json()["items"][0]["device_index"] == 1
    changed = client.patch(f"/api/v1/cameras/{camera_id}", json={"device_index": 0})
    assert changed.status_code == 200 and changed.json()["lifecycle"] == "DRAFT"
    browser = client.patch(f"/api/v1/cameras/{camera_id}", json={"source_type": "webcam"})
    assert browser.status_code == 200 and browser.json()["device_index"] is None
    assert client.patch(f"/api/v1/cameras/{camera_id}", json={"device_index": 3}).status_code == 422


def test_invalid_local_device_configuration_is_rejected():
    client = TestClient(
        create_app(
            camera_service=CameraService(
                InMemoryCameraRepository(),
                FernetCameraSecretProtector.generate(),
            )
        )
    )
    for device_index in (-1, 64, True):
        assert (
            client.post(
                "/api/v1/cameras",
                json={
                    "name": "Invalid",
                    "source_type": "local_webcam",
                    "device_index": device_index,
                },
            ).status_code
            == 422
        )
