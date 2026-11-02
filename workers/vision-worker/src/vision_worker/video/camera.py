from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from time import sleep
from typing import Protocol
from uuid import uuid4

from fr_contracts.runtime_status import CameraConnectionState

from vision_worker.video.frame import VideoFrame


@dataclass(frozen=True, slots=True)
class CameraStateSnapshot:
    camera_id: str
    state: CameraConnectionState
    reconnect_attempts: int
    total_reconnects: int
    last_connected_at: str | None
    last_error: str | None


class CameraStateMachine:
    def __init__(self, camera_id: str) -> None:
        self._camera_id = camera_id
        self._state = CameraConnectionState.DISCONNECTED
        self._reconnect_attempts = 0
        self._total_reconnects = 0
        self._last_error: str | None = None
        self._last_connected_at: str | None = None

    def connecting(self) -> None:
        self._state = (
            CameraConnectionState.RECONNECTING
            if self._reconnect_attempts
            else CameraConnectionState.CONNECTING
        )

    def connected(self) -> None:
        self._state = CameraConnectionState.CONNECTED
        self._last_connected_at = datetime.now(UTC).isoformat()
        self._reconnect_attempts = 0
        self._last_error = None

    def disconnected(self, error: str) -> None:
        self._reconnect_attempts += 1
        self._total_reconnects += 1
        self._last_error = error
        self._state = CameraConnectionState.RECONNECTING

    def failed(self, error: str) -> None:
        self._last_error = error
        self._state = CameraConnectionState.ERROR

    def stopped(self) -> None:
        self._state = CameraConnectionState.STOPPED

    @property
    def snapshot(self) -> CameraStateSnapshot:
        return CameraStateSnapshot(
            camera_id=self._camera_id,
            state=self._state,
            reconnect_attempts=self._reconnect_attempts,
            total_reconnects=self._total_reconnects,
            last_connected_at=self._last_connected_at,
            last_error=self._last_error,
        )


class GStreamerBackend(Protocol):
    def open(self, uri: str, codec: str, transport: str) -> None: ...

    def read(self, camera_id: str, session_id: str, sequence: int) -> VideoFrame | None: ...

    def close(self) -> None: ...


class FramePending(Exception):
    """A live bounded source currently has no newer frame."""


class GStreamerCameraSource:
    """Owns camera connection/reconnect behavior around interchangeable backends."""

    def __init__(
        self,
        camera_id: str,
        uri: str,
        codec: str,
        transport: str,
        backend: GStreamerBackend,
        max_reconnect_attempts: int = 5,
        reconnect_delay_seconds: float = 1.0,
        max_reconnect_delay_seconds: float = 30.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts cannot be negative")
        if reconnect_delay_seconds < 0:
            raise ValueError("reconnect_delay_seconds cannot be negative")
        if max_reconnect_delay_seconds < reconnect_delay_seconds:
            raise ValueError("max_reconnect_delay_seconds cannot be less than reconnect delay")
        self._camera_id = camera_id
        self._uri = uri
        self._codec = codec
        self._transport = transport
        self._backend = backend
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._max_reconnect_delay_seconds = max_reconnect_delay_seconds
        self._sleeper = sleeper
        self._session_id = str(uuid4())
        self._stop_requested = Event()
        self.state = CameraStateMachine(camera_id)

    @property
    def stream_session_id(self) -> str:
        return self._session_id

    def frames(self, limit: int | None = None) -> Iterator[VideoFrame]:
        yielded = 0
        reconnects = 0
        try:
            while (limit is None or yielded < limit) and not self._stop_requested.is_set():
                self.state.connecting()
                try:
                    self._backend.open(self._uri, self._codec, self._transport)
                    self._session_id = str(uuid4())
                    self.state.connected()
                    while (limit is None or yielded < limit) and not self._stop_requested.is_set():
                        sequence = yielded + 1
                        try:
                            frame = self._backend.read(self._camera_id, self._session_id, sequence)
                        except FramePending:
                            continue
                        if frame is None:
                            raise ConnectionError("GStreamer stream ended")
                        yielded += 1
                        self._session_id = frame.stream_session_id
                        yield frame
                except (ConnectionError, OSError, RuntimeError) as error:
                    category = (
                        str(error)
                        if str(error)
                        in {
                            "LOCAL_WEBCAM_UNAVAILABLE",
                            "LOCAL_WEBCAM_READ_FAILED",
                            "LOCAL_WEBCAM_OPEN_TIMEOUT",
                            "OPENCV_UNAVAILABLE",
                        }
                        else "CAMERA_STREAM_ERROR"
                    )
                    self._backend.close()
                    if self._stop_requested.is_set():
                        return
                    reconnects += 1
                    self.state.disconnected(category)
                    if reconnects > self._max_reconnect_attempts:
                        self.state.failed(category)
                        return
                    delay = min(
                        self._reconnect_delay_seconds * (2 ** (reconnects - 1)),
                        self._max_reconnect_delay_seconds,
                    )
                    if self._sleeper is sleep:
                        self._stop_requested.wait(delay)
                    else:
                        self._sleeper(delay)
        finally:
            self._backend.close()
            if self.state.snapshot.state not in {
                CameraConnectionState.ERROR,
                CameraConnectionState.FAILED,
            }:
                self.state.stopped()

    def close(self) -> None:
        self._stop_requested.set()
        self._backend.close()
        self.state.stopped()


__all__ = [
    "CameraConnectionState",
    "CameraStateMachine",
    "GStreamerBackend",
    "GStreamerCameraSource",
]
