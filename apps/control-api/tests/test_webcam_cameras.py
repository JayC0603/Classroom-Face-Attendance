from io import BytesIO

from auth_test_support import TestClient, create_app
from control_api.cameras import CameraService, FernetCameraSecretProtector, InMemoryCameraRepository
from PIL import Image


def api():
    return TestClient(
        create_app(
            camera_service=CameraService(
                InMemoryCameraRepository(), FernetCameraSecretProtector.generate()
            ),
            internal_camera_token="worker-test",
        )
    )


def test_webcam_create_activate_and_switch_to_rtsp_and_back():
    client = api()
    response = client.post(
        "/api/v1/cameras",
        json={
            "name": "Browser webcam",
            "source_type": "webcam",
        },
    )
    assert response.status_code == 201
    camera = response.json()
    assert camera["source_type"] == "webcam"
    assert camera["rtsp_url"] is None
    camera_id = camera["id"]
    client.post(f"/api/v1/cameras/{camera_id}/activate")
    internal = client.get(
        "/api/v1/internal/cameras/active", headers={"X-Worker-Token": "worker-test"}
    )
    assert internal.status_code == 200
    assert (
        client.patch(f"/api/v1/cameras/{camera_id}", json={"source_type": "rtsp"}).status_code
        == 422
    )
    switched = client.patch(
        f"/api/v1/cameras/{camera_id}",
        json={
            "source_type": "rtsp",
            "rtsp_url": "rtsp://alice:secret@camera.local/live",
        },
    )
    assert switched.status_code == 200
    assert switched.json()["lifecycle"] == "DRAFT"
    assert "secret" not in switched.text
    webcam = client.patch(
        f"/api/v1/cameras/{camera_id}",
        json={
            "source_type": "webcam",
        },
    )
    assert webcam.status_code == 200
    assert webcam.json()["rtsp_url"] is None


def test_reject_ambiguous_or_invalid_source_configuration():
    client = api()
    for body in (
        {"source_type": "webcam", "device_index": -1},
        {"source_type": "webcam", "device_index": 64},
        {"source_type": "webcam", "device_index": True},
        {"source_type": "webcam", "device_index": 0, "rtsp_url": "rtsp://camera/live"},
        {"source_type": "rtsp", "device_index": 0, "rtsp_url": "rtsp://camera/live"},
        {"source_type": "other"},
    ):
        assert client.post("/api/v1/cameras", json={"name": "Invalid", **body}).status_code == 422


def test_browser_stream_routes_authorize_and_deliver_only_while_session_is_live():
    client = api()
    camera = client.post(
        "/api/v1/cameras", json={"name": "Browser", "source_type": "webcam"}
    ).json()
    camera_id = camera["id"]
    session_url = f"/api/v1/cameras/{camera_id}/webcam/session"
    assert client.post(session_url).status_code == 409
    client.post(f"/api/v1/cameras/{camera_id}/activate")
    worker_headers = {"X-Worker-Token": "worker-test"}
    assert (
        client.get("/api/v1/internal/cameras/active", headers=worker_headers).json()["items"] == []
    )
    session = client.post(session_url).json()
    token = {"X-Webcam-Token": session["token"]}
    assert client.post(session_url).status_code == 409
    endpoint = f"/api/v1/cameras/{camera_id}/webcam/frames"
    assert (
        client.post(endpoint, headers={"X-Webcam-Token": "wrong"}, content=b"x").status_code == 403
    )
    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    assert client.post(endpoint, headers=token, content=out.getvalue()).status_code == 200
    delivered = client.get("/api/v1/internal/cameras/active", headers=worker_headers).json()[
        "items"
    ]
    assert delivered[0]["stream_session_id"] == session["session_id"]
    assert "token" not in str(delivered)
    frame_url = f"/api/v1/internal/cameras/{camera_id}/webcam/frame"
    params = {"session_id": session["session_id"], "after": 0}
    assert client.get(frame_url, params=params).status_code == 403
    frame = client.get(frame_url, params=params, headers=worker_headers)
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    assert (
        client.get(frame_url, params={**params, "after": 1}, headers=worker_headers).status_code
        == 204
    )
    assert client.post(endpoint, headers=token, content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert client.delete(session_url, headers=token).status_code == 204
    assert (
        client.get("/api/v1/internal/cameras/active", headers=worker_headers).json()["items"] == []
    )
