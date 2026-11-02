"""Ephemeral, latest-frame-only browser input. Nothing is persisted to disk."""

import secrets
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from threading import RLock
from time import monotonic
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

MAX_FRAME_BYTES = 1024 * 1024


class BrowserStreamError(ValueError):
    pass


@dataclass(frozen=True)
class BrowserFrame:
    session_id: str
    sequence: int
    timestamp_ms: int
    payload: bytes


@dataclass
class BrowserSession:
    id: str
    token: str
    revision: str
    last_seen: float
    latest: BrowserFrame | None = None


class BrowserStreamStore:
    def __init__(
        self,
        clock: Callable[[], float] = monotonic,
        max_sessions: int = 16,
        revision_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._clock = clock
        self._max_sessions = max_sessions
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = RLock()
        self._revision_resolver = revision_resolver

    def _check_revision(self, camera_id: str, revision: str) -> None:
        if self._revision_resolver is not None and self._revision_resolver(camera_id) != revision:
            raise BrowserStreamError("WEBCAM_CAMERA_CHANGED")

    def _purge(self) -> None:
        now = self._clock()
        for camera_id in list(self._sessions):
            if now - self._sessions[camera_id].last_seen >= 10:
                del self._sessions[camera_id]

    def current(self, camera_id: str, revision: str) -> BrowserSession | None:
        with self._lock:
            self._purge()
            session = self._sessions.get(camera_id)
            if session is not None and session.revision != revision:
                return None
            return session

    def start(self, camera_id: str, revision: str) -> BrowserSession:
        with self._lock:
            self._check_revision(camera_id, revision)
            if self.current(camera_id, revision) is not None:
                raise BrowserStreamError("WEBCAM_IN_USE")
            if camera_id not in self._sessions and len(self._sessions) >= self._max_sessions:
                raise BrowserStreamError("WEBCAM_SESSION_LIMIT")
            session = BrowserSession(
                str(uuid4()), secrets.token_urlsafe(32), revision, self._clock()
            )
            self._sessions[camera_id] = session
            return session

    def authorize(self, camera_id: str, revision: str, token: str) -> BrowserSession:
        with self._lock:
            self._check_revision(camera_id, revision)
            session = self.current(camera_id, revision)
            if session is None or not secrets.compare_digest(session.token, token):
                raise BrowserStreamError("WEBCAM_SESSION_UNAUTHORIZED")
            return session

    def stop(self, camera_id: str, revision: str, token: str) -> None:
        with self._lock:
            self.authorize(camera_id, revision, token)
            del self._sessions[camera_id]

    def revoke_camera(self, camera_id: str) -> None:
        with self._lock:
            self._sessions.pop(camera_id, None)

    def push(self, camera_id: str, revision: str, token: str, payload: bytes) -> BrowserFrame:
        self.authorize(camera_id, revision, token)
        if len(payload) > MAX_FRAME_BYTES:
            raise BrowserStreamError("WEBCAM_FRAME_TOO_LARGE")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as image:
                    if (
                        image.format != "JPEG"
                        or image.width > 1280
                        or image.height > 1280
                        or image.width * image.height > 921600
                    ):
                        raise BrowserStreamError("INVALID_WEBCAM_FRAME")
                    image.load()
                    clean = Image.new("RGB", image.size)
                    clean.paste(image.convert("RGB"))
                    output = BytesIO()
                    clean.save(output, format="JPEG", quality=85)
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as error:
            raise BrowserStreamError("INVALID_WEBCAM_FRAME") from error
        encoded = output.getvalue()
        if len(encoded) > MAX_FRAME_BYTES:
            raise BrowserStreamError("WEBCAM_FRAME_TOO_LARGE")
        with self._lock:
            session = self.authorize(camera_id, revision, token)
            now = self._clock()
            frame = BrowserFrame(
                session.id,
                session.latest.sequence + 1 if session.latest else 1,
                int(now * 1000),
                encoded,
            )
            session.last_seen = now
            session.latest = frame
            return frame

    def frame(
        self,
        camera_id: str,
        revision: str,
        session_id: str,
        after: int,
    ) -> BrowserFrame | None:
        with self._lock:
            self._check_revision(camera_id, revision)
            session = self.current(camera_id, revision)
            if session is None or session.id != session_id:
                raise BrowserStreamError("WEBCAM_SESSION_ENDED")
            latest = session.latest
            return latest if latest is not None and latest.sequence > after else None
