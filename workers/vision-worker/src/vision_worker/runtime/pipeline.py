from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from fr_contracts.runtime_status import (
    CameraConnectionState,
    FacePipelineRuntimeStatus,
    PersonDetectorRuntimeStatus,
    RuntimeStatus,
)
from fr_domain.models import TrackKey

from vision_worker.face_recognition.embedding_pipeline import (
    FaceObservationStatus,
    FacePipelineError,
    TrackFaceObservation,
    TrackFaceProcessor,
)
from vision_worker.inference.interfaces import BoundingBox, PersonDetector
from vision_worker.runtime.track_store import InMemoryTrackStore
from vision_worker.tracking.bot_sort import TrackerRuntimeError
from vision_worker.tracking.interfaces import TrackedPerson, Tracker
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


@dataclass(frozen=True, slots=True)
class FrameProcessingResult:
    camera_id: str
    sequence: int
    track_results: list[TrackedPerson]
    person_detections: list[BoundingBox] = field(default_factory=list)
    face_observations: list[TrackFaceObservation] = field(default_factory=list)


class RuntimePipeline:
    def __init__(
        self,
        person_detector: PersonDetector,
        tracker: Tracker | None,
        track_store: InMemoryTrackStore,
        ring_buffer: BoundedFrameBuffer,
        sampler: FrameSampler,
        worker_id: str,
        active_config_revision: str | None,
        clock: Callable[[], float] = monotonic,
        person_detector_status: Callable[[], PersonDetectorRuntimeStatus] | None = None,
        face_processor: TrackFaceProcessor | None = None,
        roi_polygon: list[tuple[float, float]] | None = None,
    ) -> None:
        self._person_detector = person_detector
        self._tracker = tracker
        self._track_store = track_store
        self._ring_buffer = ring_buffer
        self._sampler = sampler
        self._worker_id = worker_id
        self._active_config_revision = active_config_revision
        self._clock = clock
        self._person_detector_status = person_detector_status
        self._face_processor = face_processor
        self._roi_polygon = roi_polygon or []
        self._started_at = clock()
        self._input_frames = 0
        self._processed_frames = 0
        self._sampled_frames = 0
        self._dropped_frames = 0
        self._created_tracks = 0
        self._expired_tracks = 0
        self._tracker_updates = 0
        self._tracker_failures = 0
        self._tracker_total_ms = 0.0
        self._latest_tracker_ms: float | None = None
        self._latest_processing_ms: float | None = None
        self._last_camera_id: str | None = None
        self._sessions: dict[str, str] = {}
        self._latest_face_error: str | None = None

    def process_frame(self, frame: VideoFrame) -> FrameProcessingResult | None:
        previous_session = self._sessions.get(frame.camera_id)
        if previous_session is not None and previous_session != frame.stream_session_id:
            self._track_store.clear_camera(frame.camera_id)
            if self._tracker is not None:
                self._tracker.reset(frame.camera_id)
        self._sessions[frame.camera_id] = frame.stream_session_id
        self._last_camera_id = frame.camera_id
        self._input_frames += 1
        self._ring_buffer.append(frame)
        self._expired_tracks += self._track_store.evict_expired(now_ms=frame.timestamp_ms)
        if not self._sampler.should_process(frame):
            return None

        self._sampled_frames += 1
        processing_started = self._clock()
        detections = self._person_detector.detect([frame])[0]
        if len(self._roi_polygon) >= 3:
            detections = [d for d in detections if self._in_roi(d, frame.width, frame.height)]
        tracks: list[TrackedPerson] = []
        if self._tracker is not None:
            tracking_started = self._clock()
            try:
                tracks = self._tracker.update(frame, detections)
            except Exception as error:
                self._tracker_failures += 1
                if isinstance(error, TrackerRuntimeError):
                    raise
                raise TrackerRuntimeError("TRACKER_UPDATE_FAILED") from error
            self._latest_tracker_ms = (self._clock() - tracking_started) * 1000
            self._tracker_total_ms += self._latest_tracker_ms
            self._tracker_updates += 1
        active_ids = {track.track_id for track in tracks}
        self._track_store.mark_lost_except(frame.camera_id, frame.stream_session_id, active_ids)
        face_observations: list[TrackFaceObservation] = []
        self._latest_face_error = None
        for track in tracks:
            key = TrackKey(frame.camera_id, frame.stream_session_id, track.track_id)
            if self._track_store.get(key) is None:
                self._created_tracks += 1
            state = self._track_store.get_or_create(
                key, seen_at_ms=frame.timestamp_ms, box=track.box
            )
            if self._face_processor is not None:
                try:
                    observation = self._face_processor.process(frame, track, state)
                except FacePipelineError as error:
                    # The processor records/raises technical failures. Preserve a typed
                    # current observation while allowing other tracks and retries to run.
                    if state.last_face_result != FaceObservationStatus.MODEL_FAILURE.value:
                        raise
                    self._latest_face_error = str(error)
                    observation = TrackFaceObservation(
                        frame.camera_id, frame.stream_session_id, track.track_id,
                        frame.sequence, frame.timestamp_ms, FaceObservationStatus.MODEL_FAILURE,
                    )
                if observation is not None:
                    face_observations.append(observation)
        self._processed_frames += 1
        self._latest_processing_ms = (self._clock() - processing_started) * 1000
        return FrameProcessingResult(
            frame.camera_id, frame.sequence, list(tracks), detections, face_observations
        )

    def _in_roi(self, box: BoundingBox, width: int, height: int) -> bool:
        # Use the bottom-center point so people count when their feet enter a doorway zone.
        x = ((box.x1 + box.x2) / 2) / width
        y = box.y2 / height
        inside = False
        points = self._roi_polygon
        j = len(points) - 1
        for i, (xi, yi) in enumerate(points):
            xj, yj = points[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
        return inside

    def close(self) -> None:
        if self._tracker is not None:
            for camera_id in self._sessions:
                self._tracker.reset(camera_id)
        self._track_store.clear_all()
        self._ring_buffer.clear()
        self._sampler.clear()
        self._sessions.clear()

    def status(
        self,
        camera_state: CameraConnectionState,
        elapsed_seconds: float | None = None,
    ) -> RuntimeStatus:
        elapsed = max(
            elapsed_seconds if elapsed_seconds is not None else self._clock() - self._started_at,
            1e-9,
        )
        usage = self._ring_buffer.usage
        face_status = (
            self._face_processor.status() if self._face_processor else FacePipelineRuntimeStatus()
        )
        if self._latest_face_error is not None:
            face_status.state = "ERROR"
            face_status.latest_error_category = self._latest_face_error
        if (
            camera_state is CameraConnectionState.CONNECTED
            and self._tracker is not None
            and self._tracker.adapter_kind in {"bot_sort", "byte_track"}
            and self._person_detector.adapter_kind == "yolo_onnx"
            and self._tracker_failures == 0
        ):
            health: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        elif camera_state in {CameraConnectionState.ERROR, CameraConnectionState.FAILED}:
            health = "unhealthy"
        else:
            health = "degraded"
        if face_status.state == "ERROR":
            health = "unhealthy"
        return RuntimeStatus(
            worker_id=self._worker_id,
            camera_id=self._last_camera_id or "unassigned",
            camera_state=camera_state,
            input_fps=self._input_frames / elapsed,
            processed_fps=self._processed_frames / elapsed,
            dropped_frames=self._dropped_frames,
            received_frames=self._input_frames,
            sampled_frames=self._sampled_frames,
            processed_frames=self._processed_frames,
            ring_buffer_overwrites=self._ring_buffer.overwritten_frames,
            active_tracks=self._track_store.active_count,
            lost_tracks=self._track_store.lost_count,
            created_tracks=self._created_tracks,
            expired_tracks=self._expired_tracks,
            tracker_state=(
                "ERROR" if self._tracker_failures else
                "ACTIVE" if self._tracker is not None and
                self._tracker.adapter_kind in {"bot_sort", "byte_track"} else "UNAVAILABLE"
            ),
            tracker_updates=self._tracker_updates,
            tracker_failures=self._tracker_failures,
            latest_tracker_latency_ms=self._latest_tracker_ms,
            mean_tracker_latency_ms=(
                self._tracker_total_ms / self._tracker_updates if self._tracker_updates else None
            ),
            latest_processing_latency_ms=self._latest_processing_ms,
            ring_buffer_frames=usage.frames,
            ring_buffer_capacity=usage.capacity,
            worker_health=health,
            active_config_revision=self._active_config_revision,
            person_detector_runtime=(
                self._person_detector_status() if self._person_detector_status else None
            ),
            face_pipeline_runtime=face_status,
        )
