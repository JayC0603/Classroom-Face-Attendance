"""Worker-owned webcam with a single latest-frame slot and raw RGB output."""

import os
from collections.abc import Callable
from importlib import import_module
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import monotonic_ns
from typing import Any

from vision_worker.video.camera import FramePending
from vision_worker.video.frame import VideoFrame


def _capture(index: int) -> Any:
    try:
        cv2 = import_module("cv2")
    except ImportError as error:
        raise RuntimeError("OPENCV_UNAVAILABLE") from error
    return cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)


class LocalWebcamBackend:
    def __init__(
        self,
        device_index: int,
        width: int,
        height: int,
        capture_factory: Callable[[int], Any] = _capture,
    ) -> None:
        if type(device_index) is not int or not 0 <= device_index <= 63:
            raise ValueError("INVALID_WEBCAM_DEVICE")
        if width <= 0 or height <= 0:
            raise ValueError("INVALID_WEBCAM_DIMENSIONS")
        self._index, self._width, self._height = device_index, width, height
        self._factory = capture_factory
        self._latest: Queue[tuple[int, int, int, bytes]] = Queue(maxsize=1)
        self._stop = Event()
        self._ready = Event()
        self._thread: Thread | None = None
        self._lifecycle = RLock()
        self._open_error: str | None = None
        self._read_error: str | None = None

    def open(self, uri: str, codec: str, transport: str) -> None:
        with self._lifecycle:
            self._open()

    def _open(self) -> None:
        self.close()
        self._latest = Queue(maxsize=1)
        self._stop.clear()
        self._ready.clear()
        self._open_error = None
        self._read_error = None
        self._thread = Thread(target=self._capture_loop, daemon=True, name="local-webcam-reader")
        self._thread.start()
        if not self._ready.wait(timeout=5):
            self._stop.set()
            raise RuntimeError("LOCAL_WEBCAM_OPEN_TIMEOUT")
        if self._open_error:
            raise RuntimeError(self._open_error)

    def _capture_loop(self) -> None:
        capture = None
        opened = False
        try:
            capture = self._factory(self._index)
            if not capture.isOpened():
                raise RuntimeError("LOCAL_WEBCAM_UNAVAILABLE")
            # Standard OpenCV property IDs: width, height, driver buffer size.
            capture.set(3, self._width)
            capture.set(4, self._height)
            capture.set(38, 1)
            opened = True
            self._ready.set()
            while not self._stop.is_set():
                success, image = capture.read()
                if not success or image is None:
                    raise RuntimeError("LOCAL_WEBCAM_READ_FAILED")
                if image.ndim != 3 or image.shape[2] != 3 or str(image.dtype) != "uint8":
                    raise RuntimeError("INVALID_WEBCAM_FRAME")
                height, width = image.shape[:2]
                scale = min(1.0, self._width / width, self._height / height)
                if scale < 1:
                    cv2 = import_module("cv2")
                    image = cv2.resize(
                        image, (max(1, int(width * scale)), max(1, int(height * scale)))
                    )
                    height, width = image.shape[:2]
                rgb = image[:, :, ::-1].tobytes()
                frame = (width, height, monotonic_ns() // 1_000_000, rgb)
                try:
                    self._latest.put_nowait(frame)
                except Full:
                    try:
                        self._latest.get_nowait()
                    except Empty:
                        pass
                    self._latest.put_nowait(frame)
        except Exception as error:
            category = (
                str(error)
                if isinstance(error, RuntimeError)
                and str(error)
                in {
                    "OPENCV_UNAVAILABLE",
                    "LOCAL_WEBCAM_UNAVAILABLE",
                    "LOCAL_WEBCAM_READ_FAILED",
                    "INVALID_WEBCAM_FRAME",
                }
                else "LOCAL_WEBCAM_FAILED"
            )
            if opened:
                self._read_error = category
            else:
                self._open_error = category
        finally:
            if capture is not None:
                capture.release()
            self._ready.set()

    def read(self, camera_id: str, session_id: str, sequence: int) -> VideoFrame | None:
        if self._stop.is_set():
            return None
        try:
            width, height, timestamp, rgb = self._latest.get(timeout=0.2)
        except Empty:
            if self._read_error:
                raise RuntimeError(self._read_error) from None
            if self._stop.is_set():
                return None
            raise FramePending from None
        return VideoFrame(camera_id, session_id, sequence, timestamp, width, height, "RGB", rgb)

    def close(self) -> None:
        self._stop.set()
        with self._lifecycle:
            self._stop.set()
            reader = self._thread
            if reader is not None:
                reader.join(timeout=2)
                if reader.is_alive():
                    raise RuntimeError("LOCAL_WEBCAM_SHUTDOWN_TIMEOUT")
                self._thread = None
