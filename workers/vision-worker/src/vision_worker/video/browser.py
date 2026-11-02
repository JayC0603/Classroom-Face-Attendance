from io import BytesIO
from threading import Event
from time import monotonic

import httpx
from PIL import Image

from vision_worker.video.camera import FramePending
from vision_worker.video.frame import VideoFrame


class HttpBrowserCameraBackend:
    def __init__(
        self,
        api_url: str,
        camera_id: str,
        session_id: str,
        worker_token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoint = f"{api_url.rstrip('/')}/api/v1/internal/cameras/{camera_id}/webcam/frame"
        self._session_id = session_id
        self._token = worker_token
        self._provided = client
        self._client = client
        self._stop = Event()
        self._after = 0

    def open(self, uri: str, codec: str, transport: str) -> None:
        self._stop.clear()
        if self._client is None:
            self._client = httpx.Client(timeout=2.0)

    def read(self, camera_id: str, session_id: str, sequence: int) -> VideoFrame | None:
        deadline = monotonic() + 1.0
        while not self._stop.is_set() and monotonic() < deadline:
            client = self._client
            if client is None:
                return None
            try:
                response = client.get(
                    self._endpoint,
                    params={"session_id": self._session_id, "after": self._after},
                    headers={"X-Worker-Token": self._token},
                )
                if response.status_code == 204:
                    self._stop.wait(0.05)
                    continue
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise ConnectionError("BROWSER_STREAM_UNAVAILABLE") from error
            if len(response.content) > 1024 * 1024:
                raise RuntimeError("INVALID_BROWSER_FRAME")
            try:
                actual_session = response.headers["X-Stream-Session-Id"]
                actual_sequence = int(response.headers["X-Frame-Sequence"])
                timestamp = int(response.headers["X-Frame-Timestamp-Ms"])
                if actual_session != self._session_id or actual_sequence <= self._after:
                    raise ValueError("INVALID_FRAME_ORDER")
                with Image.open(BytesIO(response.content)) as image:
                    if (
                        image.format != "JPEG"
                        or image.width > 1280
                        or image.height > 1280
                        or image.width * image.height > 921600
                    ):
                        raise ValueError("INVALID_FRAME_DIMENSIONS")
                    rgb = image.convert("RGB")
                    width, height = rgb.size
                    payload = rgb.tobytes()
            except (OSError, ValueError, KeyError) as error:
                raise RuntimeError("INVALID_BROWSER_FRAME") from error
            self._after = actual_sequence
            return VideoFrame(
                camera_id,
                actual_session,
                actual_sequence,
                timestamp,
                width,
                height,
                "RGB",
                payload,
            )
        if self._stop.is_set():
            return None
        raise FramePending

    def close(self) -> None:
        self._stop.set()
        if self._provided is None:
            client, self._client = self._client, None
            if client is not None:
                client.close()
