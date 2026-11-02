"""Real camera ingestion while models are absent; never fabricates inference."""

from datetime import UTC, datetime
from threading import Thread
from time import monotonic

from fr_contracts.runtime_status import CameraConnectionState, RuntimeStatus

from vision_worker.camera_config import ActiveCamera
from vision_worker.runtime.status_publisher import (
    BestEffortRuntimeStatusPublisher,
    RuntimeStatusPublisher,
)
from vision_worker.video.camera import GStreamerCameraSource


class CaptureOnlyRuntime:
    def __init__(
        self,
        source: GStreamerCameraSource,
        config: ActiveCamera,
        worker_id: str,
        publisher: RuntimeStatusPublisher,
    ) -> None:
        self._source = source
        self._config = config
        self._worker_id = worker_id
        self._publisher = BestEffortRuntimeStatusPublisher(publisher)
        self._received = 0
        self._started = monotonic()
        self._last_frame_at: str | None = None
        self._error = "PERSON_DETECTOR_NOT_CONFIGURED"
        self._thread = Thread(target=self._run, daemon=True, name="capture-only-runtime")
        self._thread.start()

    def _publish(self) -> None:
        snapshot = self._source.state.snapshot
        self._publisher.publish(
            RuntimeStatus(
                worker_id=self._worker_id,
                camera_id=self._config.id,
                camera_state=snapshot.state,
                input_fps=self._received / max(0.001, monotonic() - self._started),
                processed_fps=0,
                dropped_frames=0,
                received_frames=self._received,
                active_tracks=0,
                ring_buffer_frames=0,
                ring_buffer_capacity=1,
                worker_health="degraded",
                active_config_revision=self._config.revision,
                stream_session_id=self._source.stream_session_id,
                reconnect_count=snapshot.total_reconnects,
                last_frame_at=self._last_frame_at,
                last_connected_at=snapshot.last_connected_at,
                error_category=(
                    snapshot.last_error
                    if snapshot.last_error
                    and (
                        snapshot.last_error.startswith("LOCAL_WEBCAM_")
                        or snapshot.last_error == "OPENCV_UNAVAILABLE"
                    )
                    else "CAMERA_CONNECTION_FAILED"
                    if snapshot.last_error
                    else self._error
                ),
            )
        )

    def _run(self) -> None:
        try:
            for _ in self._source.frames():
                self._received += 1
                self._last_frame_at = datetime.now(UTC).isoformat()
                self._publish()
        except Exception:
            self._error = "CAMERA_RUNTIME_FAILED"
        finally:
            # frames() already releases its backend on failure. Preserve that state
            # until the operator explicitly stops or replaces this runtime.
            if self._source.state.snapshot.state not in {
                CameraConnectionState.ERROR,
                CameraConnectionState.FAILED,
            }:
                self._source.close()
            try:
                self._publish()
            except Exception:
                pass

    def close(self) -> None:
        self._source.close()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("CAMERA_RUNTIME_SHUTDOWN_TIMEOUT")
