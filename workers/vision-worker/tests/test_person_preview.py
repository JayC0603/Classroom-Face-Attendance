import json
from threading import Event

import httpx
from vision_worker.inference.interfaces import BoundingBox
from vision_worker.video.frame import VideoFrame
from vision_worker.video.preview import WorkerPreviewPublisher


def test_processed_preview_keeps_person_boxes_with_its_exact_frame_and_model():
    event = Event()
    requests = []

    def handle(request):
        requests.append(request)
        event.set()
        return httpx.Response(204)

    publisher = WorkerPreviewPublisher(
        "http://api",
        "cam",
        "rev",
        "token",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )
    frame = VideoFrame("cam", "stream", 10, 1, 100, 200, "RGB", b"\x00" * (100 * 200 * 3))
    publisher.offer(frame, [BoundingBox(10, 20, 50, 180, 0.8)], "model-123")
    assert event.wait(timeout=2)
    publisher.close()
    assert requests[0].headers["X-Person-Deployment"] == "model-123"
    boxes = json.loads(requests[0].headers["X-Person-Detections"])
    assert boxes[0]["box"] == [0.1, 0.1, 0.4, 0.8]
    assert boxes[0]["confidence"] == 0.8
