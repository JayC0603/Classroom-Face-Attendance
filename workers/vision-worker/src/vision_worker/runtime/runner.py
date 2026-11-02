from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fr_contracts.runtime_status import CameraConnectionState

from vision_worker.runtime.pipeline import FrameProcessingResult, RuntimePipeline
from vision_worker.runtime.status_publisher import (
    BestEffortRuntimeStatusPublisher,
    RuntimeStatusPublisher,
)
from vision_worker.video.camera import GStreamerCameraSource
from vision_worker.video.frame import VideoFrame


@dataclass(frozen=True, slots=True)
class RuntimeRunSummary:
    frames_received: int
    frames_processed: int
    camera_state: CameraConnectionState
    last_result: FrameProcessingResult | None


class RuntimeRunner:
    """Connects a camera source to the synchronous per-camera runtime pipeline."""

    def __init__(
        self,
        source: GStreamerCameraSource,
        pipeline: RuntimePipeline,
        status_publisher: RuntimeStatusPublisher | None = None,
        processed_frame_callback: Callable[[VideoFrame, FrameProcessingResult], None] | None = None,
    ) -> None:
        self._source = source
        self._pipeline = pipeline
        self._processed_frame_callback = processed_frame_callback
        self._status_publisher = (
            BestEffortRuntimeStatusPublisher(status_publisher) if status_publisher else None
        )
        self._last_frame_at: str | None = None

    def run(self, limit: int | None = None) -> RuntimeRunSummary:
        received = 0
        processed = 0
        last_result: FrameProcessingResult | None = None
        for frame in self._source.frames(limit=limit):
            received += 1
            self._last_frame_at = datetime.now(UTC).isoformat()
            result = self._pipeline.process_frame(frame)
            if result is not None:
                processed += 1
                last_result = result
                if self._processed_frame_callback is not None:
                    self._processed_frame_callback(frame, result)
            self._publish_status()
        self._publish_status()
        return RuntimeRunSummary(
            frames_received=received,
            frames_processed=processed,
            camera_state=self._source.state.snapshot.state,
            last_result=last_result,
        )

    def _publish_status(self) -> None:
        if self._status_publisher is None:
            return
        snapshot = self._source.state.snapshot
        pipeline_status = self._pipeline.status(camera_state=snapshot.state)
        status = pipeline_status.model_copy(
            update={
                "stream_session_id": self._source.stream_session_id,
                "reconnect_count": snapshot.total_reconnects,
                "last_connected_at": snapshot.last_connected_at,
                "last_frame_at": self._last_frame_at,
                "error_category": (
                    "CAMERA_CONNECTION_FAILED"
                    if snapshot.last_error
                    else "MODEL_INFERENCE_ERROR"
                    if pipeline_status.face_pipeline_runtime.state == "ERROR"
                    else None
                ),
            }
        )
        self._status_publisher.publish(status)
