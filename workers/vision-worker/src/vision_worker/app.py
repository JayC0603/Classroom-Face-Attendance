from collections.abc import Callable

from fr_config.models import FaceRecognitionConfig
from fr_contracts.runtime_status import (
    CameraConnectionState,
    PersonDetectorRuntimeStatus,
    RuntimeStatus,
)

from vision_worker.face_recognition.embedding_pipeline import TrackFaceProcessor
from vision_worker.inference.interfaces import PersonDetector
from vision_worker.inference.stubs import StubPersonDetector
from vision_worker.runtime.pipeline import FrameProcessingResult, RuntimePipeline
from vision_worker.runtime.runner import RuntimeRunner, RuntimeRunSummary
from vision_worker.runtime.status_publisher import RuntimeStatusPublisher
from vision_worker.runtime.track_store import InMemoryTrackStore
from vision_worker.tracking.interfaces import StubTracker, Tracker
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.camera import GStreamerCameraSource
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


class VisionWorker:
    def __init__(
        self,
        pipeline: RuntimePipeline,
        processed_frame_callback: Callable[[VideoFrame, FrameProcessingResult], None] | None = None,
        roi_polygon: list[tuple[float, float]] | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._processed_frame_callback = processed_frame_callback

    @classmethod
    def with_stub_adapters(cls) -> "VisionWorker":
        pipeline = RuntimePipeline(
            person_detector=StubPersonDetector(),
            tracker=StubTracker(),
            track_store=InMemoryTrackStore(ttl_ms=10000),
            ring_buffer=BoundedFrameBuffer(capacity=100),
            sampler=FrameSampler(target_fps=5.0),
            worker_id="worker-local",
            active_config_revision=None,
        )
        return cls(pipeline)

    @classmethod
    def with_runtime_config(
        cls,
        config: FaceRecognitionConfig,
        worker_id: str,
        config_revision: str,
        processing_fps: float,
        ring_buffer_capacity: int,
        person_detector: PersonDetector | None = None,
        tracker: Tracker | None = None,
        person_detector_status: Callable[[], PersonDetectorRuntimeStatus] | None = None,
        face_processor: TrackFaceProcessor | None = None,
        processed_frame_callback: Callable[[VideoFrame, FrameProcessingResult], None] | None = None,
        roi_polygon: list[tuple[float, float]] | None = None,
    ) -> "VisionWorker":
        if person_detector is None:
            raise RuntimeError("PERSON_DETECTOR_NOT_CONFIGURED")
        pipeline = RuntimePipeline(
            person_detector=person_detector,
            tracker=tracker,
            track_store=InMemoryTrackStore(ttl_ms=config.timing.track_cache_ttl_ms),
            ring_buffer=BoundedFrameBuffer(capacity=ring_buffer_capacity),
            sampler=FrameSampler(target_fps=processing_fps),
            worker_id=worker_id,
            active_config_revision=config_revision,
            person_detector_status=person_detector_status,
            face_processor=face_processor,
            roi_polygon=roi_polygon,
        )
        return cls(pipeline, processed_frame_callback)

    def process_frame(self, frame: VideoFrame) -> FrameProcessingResult | None:
        return self._pipeline.process_frame(frame)

    def status(self, camera_state: CameraConnectionState) -> RuntimeStatus:
        return self._pipeline.status(camera_state)

    def close(self) -> None:
        self._pipeline.close()

    def run_camera(
        self,
        source: GStreamerCameraSource,
        limit: int | None = None,
        status_publisher: RuntimeStatusPublisher | None = None,
    ) -> RuntimeRunSummary:
        return RuntimeRunner(
            source, self._pipeline, status_publisher, self._processed_frame_callback
        ).run(limit=limit)

    def run_once(self) -> dict[str, object]:
        runtime = self._pipeline.status(
            camera_state=CameraConnectionState.DISCONNECTED,
            elapsed_seconds=1.0,
        ).model_dump(mode="json")
        return {
            "status": "ready",
            "inference_mode": "stub",
            "camera_count": 0,
            **runtime,
        }
