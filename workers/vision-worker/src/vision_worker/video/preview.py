"""Best-effort preview outside the inference path, with one pending RGB frame."""

import json
from collections.abc import Callable, Iterator, Sequence
from io import BytesIO
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic

import httpx
from PIL import Image

from vision_worker.inference.interfaces import BoundingBox
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.camera import GStreamerCameraSource
from vision_worker.video.frame import VideoFrame


class WorkerPreviewPublisher:
    def __init__(
        self,
        api_url: str,
        camera_id: str,
        revision: str,
        token: str,
        client_factory: Callable[[], httpx.Client] | None = None,
        display_fps: float = 15.0,
    ) -> None:
        self._url = f"{api_url.rstrip('/')}/api/v1/internal/cameras/{camera_id}/preview"
        self._revision, self._token = revision, token
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=1.0))
        self._pending: Queue[tuple[VideoFrame, str, str]] = Queue(maxsize=1)
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._last_offered = float("-inf")
        self._interval = 1.0 / max(1.0, min(display_fps, 30.0))
        self._latest_detections: list[BoundingBox | TrackedPerson] = []
        self._latest_deployment_id = ""

    def set_detections(self, detections: list[TrackedPerson], deployment_id: str) -> None:
        with self._lock:
            self._latest_detections = list(detections[:25])
            self._latest_deployment_id = deployment_id

    def offer(
        self,
        frame: VideoFrame,
        detections: Sequence[BoundingBox | TrackedPerson] | None = None,
        deployment_id: str = "",
    ) -> None:
        with self._lock:
            if self._stop.is_set() or monotonic() - self._last_offered < self._interval:
                return
            if (
                frame.pixel_format != "RGB"
                or not isinstance(frame.payload, bytes)
                or frame.width <= 0
                or frame.height <= 0
                or len(frame.payload) != frame.width * frame.height * 3
            ):
                return
            self._last_offered = monotonic()
            if detections is None:
                detections = self._latest_detections
                deployment_id = self._latest_deployment_id
            boxes: list[dict[str, object]] = []
            for detection in (detections or [])[:25]:
                track_id: int | None = None
                if isinstance(detection, TrackedPerson):
                    box = detection.box
                    track_id = detection.track_id
                else:
                    box = detection
                geometry = [
                    round(box.x1 / frame.width, 4),
                    round(box.y1 / frame.height, 4),
                    round((box.x2 - box.x1) / frame.width, 4),
                    round((box.y2 - box.y1) / frame.height, 4),
                ]
                if geometry[2] > 0 and geometry[3] > 0:
                    item: dict[str, object] = {
                        "box": geometry,
                        "confidence": round(box.confidence, 4),
                    }
                    if track_id is not None:
                        item["track_id"] = track_id
                    boxes.append(item)
            queued_item = (frame, json.dumps(boxes, separators=(",", ":")), deployment_id)
            try:
                self._pending.put_nowait(queued_item)
            except Full:
                try:
                    self._pending.get_nowait()
                except Empty:
                    pass
                self._pending.put_nowait(queued_item)
            if self._thread is None:
                self._thread = Thread(target=self._run, daemon=True, name="camera-preview")
                self._thread.start()

    def _run(self) -> None:
        with self._client_factory() as client:
            while not self._stop.is_set():
                try:
                    frame, boxes, deployment_id = self._pending.get(timeout=0.2)
                except Empty:
                    continue
                try:
                    image = Image.frombytes("RGB", (frame.width, frame.height), frame.payload)
                    image.thumbnail((640, 480))
                    out = BytesIO()
                    image.save(out, format="JPEG", quality=70)
                    payload = out.getvalue()
                    if self._stop.is_set() or len(payload) > 256 * 1024:
                        continue
                    client.post(
                        self._url,
                        params={"revision": self._revision},
                        content=payload,
                        headers={
                            "X-Worker-Token": self._token,
                            "Content-Type": "image/jpeg",
                            "X-Preview-Session": frame.stream_session_id,
                            "X-Person-Detections": boxes,
                            "X-Person-Deployment": deployment_id,
                        },
                    )
                except (httpx.HTTPError, OSError, ValueError):
                    # Preview outages must not disconnect capture or inference.
                    continue

    def close(self) -> None:
        with self._lock:
            self._stop.set()
            reader = self._thread
        if reader is not None:
            reader.join(timeout=1.5)
        try:
            self._pending.get_nowait()
        except Empty:
            pass


class PreviewCameraSource(GStreamerCameraSource):
    preview: WorkerPreviewPublisher
    processed_preview: bool = False
    raw_preview: bool = True

    def frames(self, limit: int | None = None) -> Iterator[VideoFrame]:
        try:
            for frame in super().frames(limit):
                if self.raw_preview:
                    self.preview.offer(frame)
                yield frame
        finally:
            self.preview.close()

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.preview.close()
