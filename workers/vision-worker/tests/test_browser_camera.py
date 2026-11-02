from io import BytesIO

import httpx
from PIL import Image
from vision_worker.camera_config import ActiveCamera
from vision_worker.capture_runtime import CaptureOnlyRuntime
from vision_worker.video.browser import HttpBrowserCameraBackend
from vision_worker.video.camera import GStreamerCameraSource


def test_browser_frames_use_worker_auth_and_keep_browser_session_and_rgb():
    out = BytesIO()
    Image.new("RGB", (16, 12), "red").save(out, format="JPEG")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["X-Worker-Token"] == "worker-secret"
        assert request.url.params["session_id"] == "browser-session"
        return httpx.Response(
            200,
            content=out.getvalue(),
            headers={
                "X-Frame-Sequence": "2",
                "X-Stream-Session-Id": "browser-session",
                "X-Frame-Timestamp-Ms": "1000",
            },
        )

    backend = HttpBrowserCameraBackend(
        "http://api",
        "cam-1",
        "browser-session",
        "worker-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    backend.open("", "AUTO", "browser")
    frame = backend.read("cam-1", "worker-session", 1)
    assert frame.stream_session_id == "browser-session"
    assert frame.sequence == 2 and frame.timestamp_ms == 1000
    assert frame.pixel_format == "RGB" and (frame.width, frame.height) == (16, 12)
    assert len(frame.payload) == 16 * 12 * 3
    backend.close()


def test_real_browser_frames_are_ingested_without_fabricating_model_results():
    from threading import Event

    from vision_worker.video.frame import VideoFrame

    published = []
    got_frame = Event()

    class Backend:
        def open(self, *args):
            pass

        def read(self, camera_id, session_id, sequence):
            return VideoFrame(
                camera_id, "browser-session", sequence, sequence * 100, 1, 1, "RGB", b"\0" * 3
            )

        def close(self):
            pass

    class Publisher:
        def publish(self, value):
            if not published:
                published.append(None)
                raise httpx.ConnectError("temporary telemetry failure")
            published.append(value)
            if value.received_frames:
                got_frame.set()

    source = GStreamerCameraSource("cam", "", "AUTO", "browser", Backend())
    config = ActiveCamera(
        "cam", "Browser", None, "AUTO", True, 5, "rev", "webcam", "browser-session"
    )
    runtime = CaptureOnlyRuntime(source, config, "worker", Publisher())
    assert got_frame.wait(timeout=2)
    runtime.close()
    assert any(status.received_frames > 0 for status in published if status is not None)
    assert all(status.processed_frames == 0 for status in published if status is not None)
    assert all(
        status.error_category == "PERSON_DETECTOR_NOT_CONFIGURED"
        for status in published
        if status is not None
    )
    assert published[-1].stream_session_id == "browser-session"


def test_browser_backend_uses_the_same_detection_tracking_pipeline():
    from vision_worker.inference.interfaces import BoundingBox
    from vision_worker.inference.stubs import MockPersonDetector
    from vision_worker.runtime.pipeline import RuntimePipeline
    from vision_worker.runtime.runner import RuntimeRunner
    from vision_worker.runtime.track_store import InMemoryTrackStore
    from vision_worker.tracking.interfaces import MockTracker
    from vision_worker.video.buffer import BoundedFrameBuffer
    from vision_worker.video.sampling import FrameSampler

    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    counter = [0]

    def handler(request):
        counter[0] += 1
        return httpx.Response(
            200,
            content=out.getvalue(),
            headers={
                "X-Frame-Sequence": str(counter[0]),
                "X-Stream-Session-Id": "browser",
                "X-Frame-Timestamp-Ms": str(counter[0] * 200),
            },
        )

    backend = HttpBrowserCameraBackend(
        "http://api",
        "cam",
        "browser",
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    source = GStreamerCameraSource("cam", "", "AUTO", "browser", backend)
    pipeline = RuntimePipeline(
        person_detector=MockPersonDetector([BoundingBox(1, 1, 20, 40, confidence=0.95)]),
        tracker=MockTracker(),
        track_store=InMemoryTrackStore(ttl_ms=1000),
        ring_buffer=BoundedFrameBuffer(capacity=2),
        sampler=FrameSampler(target_fps=5),
        worker_id="worker",
        active_config_revision="r1",
    )
    summary = RuntimeRunner(source, pipeline).run(limit=2)
    assert summary.frames_received == 2 and summary.frames_processed == 2
    assert summary.last_result.track_results[0].track_id == 1
    assert source.stream_session_id == "browser"
