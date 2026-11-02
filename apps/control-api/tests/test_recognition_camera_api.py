from io import BytesIO

from auth_test_support import TestClient, create_app
from control_api.cameras import CameraService, FernetCameraSecretProtector, InMemoryCameraRepository
from PIL import Image


def test_camera_comparison_only_reads_fresh_active_revision_frames():
    class Comparison:
        calls = []

        def compare(self, payload, scope, frame_id, roi_polygon=None):
            self.calls.append((scope, frame_id, roi_polygon))
            return {"state": "NO_FACE", "faces": [], "frame_id": frame_id}

        def status(self, force=False):
            return {"state": "READY", "model_names": ["YuNet", "SFace"]}

    comparison = Comparison()
    client = TestClient(
        create_app(
            camera_service=CameraService(
                InMemoryCameraRepository(),
                FernetCameraSecretProtector.generate(),
            ),
            internal_camera_token="worker",
            face_comparison_service=comparison,
        )
    )
    camera = client.post(
        "/api/v1/cameras", json={"name": "Local", "source_type": "local_webcam"}
    ).json()
    url = f"/api/v1/cameras/{camera['id']}/recognition"
    assert client.get(url, params={"revision": camera["revision"]}).status_code == 409
    camera = client.post(f"/api/v1/cameras/{camera['id']}/activate").json()
    params = {"revision": camera["revision"]}
    assert client.get(url, params=params).json()["state"] == "WAITING_FOR_FRAME"
    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    client.post(
        f"/api/v1/internal/cameras/{camera['id']}/preview",
        params=params,
        headers={"X-Worker-Token": "worker", "X-Preview-Session": "session-one"},
        content=out.getvalue(),
    )
    result = client.get(url, params=params)
    assert result.status_code == 200 and result.json()["state"] == "NO_FACE"
    annotated = client.get(url, params={**params, "include_preview": True}).json()
    assert annotated["preview"].startswith("data:image/jpeg;base64,")
    assert annotated["frame_id"] == result.json()["frame_id"]
    assert annotated["stream_session_id"] == "session-one"
    assert comparison.calls[0][0].endswith("/session-one")
    assert comparison.calls[0][2] is None
    assert client.get(url, params={"revision": "stale"}).status_code == 409
    assert len(comparison.calls) == 2
    assert client.get("/api/v1/recognition/simple/status").json()["state"] == "READY"


def test_recognition_results_are_limited_to_the_camera_roi():
    class Comparison:
        def status(self, force=False):
            return {"state": "READY"}

        def compare(self, payload, scope, frame_id, roi_polygon=None):
            self.roi_polygon = roi_polygon
            return {"state": "READY", "faces": [
                {"box": [0.35, 0.35, 0.1, 0.1], "state": "UNKNOWN", "similarity": 0.1},
                {"box": [0.85, 0.35, 0.1, 0.1], "state": "UNKNOWN", "similarity": 0.1},
            ]}

    polygon = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    comparison = Comparison()
    client = TestClient(create_app(
        camera_service=CameraService(
            InMemoryCameraRepository(), FernetCameraSecretProtector.generate()
        ),
        internal_camera_token="worker",
        face_comparison_service=comparison,
    ))
    camera = client.post(
        "/api/v1/cameras",
        json={"name": "ROI", "source_type": "local_webcam", "roi_polygon": polygon},
    ).json()
    camera = client.post(f"/api/v1/cameras/{camera['id']}/activate").json()
    frame = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(frame, format="JPEG")
    client.post(
        f"/api/v1/internal/cameras/{camera['id']}/preview?revision={camera['revision']}",
        headers={"X-Worker-Token": "worker", "X-Preview-Session": "roi-session"},
        content=frame.getvalue(),
    )

    result = client.get(
        f"/api/v1/cameras/{camera['id']}/recognition?revision={camera['revision']}"
    ).json()

    assert len(result["faces"]) == 1
    assert result["faces"][0]["box"][0] == 0.35
    assert comparison.roi_polygon == [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]


def test_old_worker_session_result_is_discarded_after_reconnect():
    from control_api.browser_stream import BrowserStreamStore
    from control_api.camera_preview import PreviewStore
    from control_api.cameras import CameraCreate
    from control_api.simple_recognition_api import create_simple_recognition_router
    from fastapi import FastAPI

    cameras = CameraService(InMemoryCameraRepository(), FernetCameraSecretProtector.generate())
    camera = cameras.create(CameraCreate(name="Local", source_type="local_webcam"))
    camera = cameras.activate(camera.id)
    previews = PreviewStore()
    previews.put(camera.id, camera.revision, b"jpeg", "old-session")

    class Comparison:
        def compare(self, *args, **kwargs):
            previews.put(camera.id, camera.revision, b"new-jpeg", "new-session")
            return {"state": "READY", "faces": [{"state": "VERIFIED"}]}

    app = FastAPI()
    app.include_router(
        create_simple_recognition_router(Comparison(), cameras, previews, BrowserStreamStore())
    )
    result = TestClient(app).get(
        f"/api/v1/cameras/{camera.id}/recognition", params={"revision": camera.revision}
    )
    assert result.json() == {"state": "WAITING_FOR_FRAME", "faces": []}


def test_old_browser_session_result_is_discarded_after_restart():
    from control_api.browser_stream import BrowserStreamStore
    from control_api.camera_preview import PreviewStore
    from control_api.cameras import CameraCreate
    from control_api.simple_recognition_api import create_simple_recognition_router
    from fastapi import FastAPI

    cameras = CameraService(InMemoryCameraRepository(), FernetCameraSecretProtector.generate())
    camera = cameras.create(CameraCreate(name="Browser", source_type="webcam"))
    camera = cameras.activate(camera.id)
    browser = BrowserStreamStore()
    session = browser.start(camera.id, camera.revision)
    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    browser.push(camera.id, camera.revision, session.token, out.getvalue())

    class Comparison:
        def compare(self, *args, **kwargs):
            browser.stop(camera.id, camera.revision, session.token)
            browser.start(camera.id, camera.revision)
            return {"state": "READY", "faces": [{"state": "VERIFIED"}]}

    app = FastAPI()
    app.include_router(
        create_simple_recognition_router(Comparison(), cameras, PreviewStore(), browser)
    )
    result = TestClient(app).get(
        f"/api/v1/cameras/{camera.id}/recognition", params={"revision": camera.revision}
    )
    assert result.json() == {"state": "WAITING_FOR_FRAME", "faces": []}
