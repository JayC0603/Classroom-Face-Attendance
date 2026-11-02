from io import BytesIO
from threading import Event

import httpx
from PIL import Image
from vision_worker.video.frame import VideoFrame


def test_preview_delivers_bounded_jpeg_with_auth_and_stops_after_close():
    from vision_worker.video.preview import WorkerPreviewPublisher

    received = Event()
    requests = []

    def handler(request):
        requests.append(request)
        received.set()
        return httpx.Response(204)

    publisher = WorkerPreviewPublisher(
        "http://api",
        "cam",
        "rev",
        "token",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    frame = VideoFrame("cam", "session", 1, 10, 800, 600, "RGB", b"\x00\x00\xff" * (800 * 600))
    publisher.offer(frame)
    assert received.wait(timeout=2)
    publisher.close()
    publisher.offer(frame)
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["X-Worker-Token"] == "token"
    assert request.headers["X-Preview-Session"] == "session"
    assert request.url.params["revision"] == "rev"
    with Image.open(BytesIO(request.content)) as image:
        assert image.format == "JPEG" and image.size == (640, 480)
