import json
from base64 import b64decode
from io import BytesIO

import pytest
from control_api.browser_stream import BrowserStreamStore
from control_api.camera_preview import PreviewStore, create_camera_preview_router
from control_api.cameras import (
    CameraCreate,
    CameraService,
    FernetCameraSecretProtector,
    InMemoryCameraRepository,
)
from control_api.simple_recognition_api import create_simple_recognition_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def jpeg(color):
    out = BytesIO()
    Image.new("RGB", (64, 48), color).save(out, format="JPEG")
    return out.getvalue()


@pytest.mark.parametrize("source", ["local_webcam", "webcam"])
def test_person_and_face_results_use_the_exact_processed_preview(source):
    cameras = CameraService(InMemoryCameraRepository(), FernetCameraSecretProtector.generate())
    camera = cameras.activate(cameras.create(CameraCreate(name="Camera", source_type=source)).id)
    browser, previews = BrowserStreamStore(), PreviewStore()
    session_id = "worker-session"
    if source == "webcam":
        session = browser.start(camera.id, camera.revision)
        browser.push(camera.id, camera.revision, session.token, jpeg("red"))
        session_id = session.id
    calls = []

    class Comparison:
        def compare(self, payload, scope, frame_id, roi_polygon=None):
            calls.append((payload, scope, frame_id))
            return {"state": "NO_FACE", "faces": []}

    app = FastAPI()
    app.include_router(create_camera_preview_router(cameras, "worker", previews))
    app.include_router(create_simple_recognition_router(Comparison(), cameras, previews, browser))
    client = TestClient(app)
    params = {"revision": camera.revision}
    internal = f"/api/v1/internal/cameras/{camera.id}/preview"
    boxes = [{"box": [0.1, 0.2, 0.5, 0.6], "confidence": 0.8}]
    headers = {
        "X-Worker-Token": "worker",
        "X-Preview-Session": session_id,
        "X-Person-Deployment": "yolox-1",
        "X-Person-Detections": json.dumps(boxes),
    }
    payload = jpeg("blue")
    assert client.post(internal, params=params, headers=headers, content=payload).status_code == 204
    url = f"/api/v1/cameras/{camera.id}/recognition"
    result = client.get(url, params={**params, "include_preview": True}).json()
    assert result["persons"] == [{**box, "track_id": None} for box in boxes]
    assert result["person_state"] == "READY"
    assert result["person_deployment_id"] == "yolox-1"
    assert b64decode(result["preview"].split(",")[1]) == calls[0][0] == payload
    assert "/worker/yolox-1/" in calls[0][1]
    for invalid in [
        "bad",
        '[{"box":[0,0,2,1],"confidence":0.8}]',
        '[{"box":[0,0,1,1],"confidence":NaN}]',
        json.dumps(boxes * 26),
    ]:
        rejected = client.post(
            internal,
            params=params,
            headers={**headers, "X-Person-Detections": invalid},
            content=payload,
        )
        assert rejected.status_code == 422
    if source == "webcam":
        browser.stop(camera.id, camera.revision, session.token)
        replacement = browser.start(camera.id, camera.revision)
        browser.push(camera.id, camera.revision, replacement.token, jpeg("green"))
        result = client.get(url, params={**params, "include_preview": True}).json()
        assert result["person_state"] == "NOT_CONFIGURED"
        assert result["persons"] == []
        current = browser.frame(camera.id, camera.revision, replacement.id, 0)
        assert current is not None
        assert b64decode(result["preview"].split(",")[1]) == current.payload == calls[-1][0]
