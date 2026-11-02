from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier, Lock
from time import sleep

import numpy as np
import pytest
from vision_worker.video.local_webcam import LocalWebcamBackend


class Capture:
    def __init__(self, opened=True):
        self.opened = opened
        self.released = False
        self.frames = Queue()
        self.frames.put(np.full((2, 3, 3), [10, 20, 30], dtype=np.uint8))

    def isOpened(self):
        return self.opened

    def set(self, *_):
        return True

    def read(self):
        try:
            return True, self.frames.get(timeout=0.05)
        except Exception:
            return False, None

    def release(self):
        self.released = True


def test_local_webcam_returns_rgb_without_jpeg_and_releases_device():
    capture = Capture()
    backend = LocalWebcamBackend(0, 640, 480, capture_factory=lambda _: capture)
    backend.open("", "AUTO", "local")
    frame = backend.read("cam", "session", 1)
    assert frame.pixel_format == "RGB"
    assert frame.payload[:3] == bytes([30, 20, 10])
    assert len(frame.payload) == 2 * 3 * 3
    assert (frame.width, frame.height) == (3, 2)
    assert frame.timestamp_ms > 0 and frame.stream_session_id == "session"
    backend.close()
    assert capture.released


def test_unavailable_webcam_fails_categorically_and_releases():
    capture = Capture(opened=False)
    backend = LocalWebcamBackend(0, 640, 480, capture_factory=lambda _: capture)
    with pytest.raises(RuntimeError, match="LOCAL_WEBCAM_UNAVAILABLE"):
        backend.open("", "AUTO", "local")
    backend.close()
    assert capture.released


def test_device_index_is_an_integer_within_bounds():
    for index in (-1, 64, True):
        with pytest.raises(ValueError):
            LocalWebcamBackend(index, 640, 480)


def test_concurrent_close_serializes_reader_shutdown():
    backend = LocalWebcamBackend(0, 640, 480)
    barrier = Barrier(8)
    lock = Lock()
    active = 0
    maximum = 0

    class Reader:
        def join(self, timeout):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            sleep(0.02)
            with lock:
                active -= 1

        def is_alive(self):
            return False

    backend._thread = Reader()

    def close(_):
        barrier.wait(timeout=2)
        backend.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(close, range(8)))
    assert maximum == 1
    assert backend._thread is None


def test_unavailable_device_status_preserves_capture_failure():
    from threading import Event

    from vision_worker.camera_config import ActiveCamera
    from vision_worker.capture_runtime import CaptureOnlyRuntime
    from vision_worker.video.camera import GStreamerCameraSource

    published = []
    finished = Event()

    class Publisher:
        def publish(self, status):
            published.append(status)
            finished.set()

    capture = Capture(opened=False)
    backend = LocalWebcamBackend(0, 640, 480, capture_factory=lambda _: capture)
    source = GStreamerCameraSource("cam", "", "AUTO", "local", backend, max_reconnect_attempts=0)
    config = ActiveCamera(
        "cam", "Local", None, "AUTO", True, 5, "rev", "local_webcam", device_index=0
    )
    runtime = CaptureOnlyRuntime(source, config, "worker", Publisher())
    assert finished.wait(timeout=2)
    runtime._thread.join(timeout=2)
    assert published[-1].error_category == "LOCAL_WEBCAM_UNAVAILABLE"
    assert published[-1].camera_state.value == "ERROR"
    assert published[-1].received_frames == published[-1].processed_frames == 0
    assert capture.released
    runtime.close()
