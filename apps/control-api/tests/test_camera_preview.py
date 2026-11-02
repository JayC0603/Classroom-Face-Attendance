from io import BytesIO

from auth_test_support import TestClient, create_app
from control_api.cameras import CameraService, FernetCameraSecretProtector, InMemoryCameraRepository
from PIL import Image


def test_worker_preview_is_bounded_authorized_and_revision_scoped():
    client = TestClient(
        create_app(
            camera_service=CameraService(
                InMemoryCameraRepository(),
                FernetCameraSecretProtector.generate(),
            ),
            internal_camera_token="worker",
        )
    )
    camera = client.post(
        "/api/v1/cameras",
        json={
            "name": "Local",
            "source_type": "local_webcam",
        },
    ).json()
    camera = client.post(f"/api/v1/cameras/{camera['id']}/activate").json()
    url = f"/api/v1/cameras/{camera['id']}/preview"
    internal = f"/api/v1/internal/cameras/{camera['id']}/preview"
    params = {"revision": camera["revision"]}
    headers = {"X-Worker-Token": "worker"}
    assert client.get(url, params=params).status_code == 204
    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    payload = out.getvalue()
    assert client.post(internal, params=params, content=payload).status_code == 403
    assert (
        client.post(internal, params=params, headers=headers, content=b"x" * 262145).status_code
        == 413
    )
    assert client.post(internal, params=params, headers=headers, content=b"bad").status_code == 422
    assert client.post(internal, params=params, headers=headers, content=payload).status_code == 204
    response = client.get(url, params=params)
    assert response.content == payload
    assert response.headers["content-type"] == "image/jpeg"
    assert "no-store" in response.headers["cache-control"]
    client.patch(f"/api/v1/cameras/{camera['id']}", json={"device_index": 1})
    assert client.get(url, params=params).status_code == 409
    assert client.post(internal, params=params, headers=headers, content=payload).status_code == 409


def test_preview_expires_and_rejects_inactive_camera():
    from control_api.camera_preview import PreviewStore

    now = [0.0]
    store = PreviewStore(clock=lambda: now[0])
    store.put("cam", "r1", b"jpeg")
    assert store.get("cam", "r1") == b"jpeg"
    assert store.get("cam", "r2") is None
    now[0] = 3.1
    assert store.get("cam", "r1") is None
