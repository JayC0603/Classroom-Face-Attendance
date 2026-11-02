import pytest
from fr_contracts.runtime_status import CameraConnectionState
from fr_domain.models import TrackKey
from vision_worker.inference.interfaces import BoundingBox
from vision_worker.inference.stubs import MockPersonDetector
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore, TrackLifecycle
from vision_worker.tracking.bot_sort import BoTSortTracker, TrackerRuntimeError
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


def frame(sequence: int, timestamp_ms: int, session: str = "session-1") -> VideoFrame:
    return VideoFrame(
        "camera-1", session, sequence, timestamp_ms=timestamp_ms,
        width=100, height=100, pixel_format="RGB", payload=bytes(100 * 100 * 3),
    )


def box(x: float = 10) -> BoundingBox:
    return BoundingBox(x, 10, x + 20, 50, 0.9)


def test_real_bot_sort_keeps_id_across_motion_and_short_loss() -> None:
    tracker = BoTSortTracker("camera-1", processing_fps=5, track_cache_ttl_ms=1000)
    first = tracker.update(frame(1, 0), [box()])
    second = tracker.update(frame(2, 200), [box(11)])
    missing = tracker.update(frame(3, 400), [])
    recovered = tracker.update(frame(4, 600), [box(12)])
    assert len(first) == len(second) == len(recovered) == 1
    assert first[0].track_id == second[0].track_id == recovered[0].track_id
    assert missing == []
    assert tracker.created_tracks == 1
    assert tracker.update_count == 4
    assert tracker.latest_latency_ms is not None


@pytest.mark.parametrize(
    "bad", [
        BoundingBox(20, 10, 10, 50, 0.9),
        BoundingBox(-1, 10, 20, 50, 0.9),
        BoundingBox(10, 10, 20, 50, float("nan")),
        BoundingBox(10, 10, 20, 50, 1.1),
    ],
)
def test_invalid_detection_is_technical_failure(bad: BoundingBox) -> None:
    tracker = BoTSortTracker("camera-1", 5, 1000)
    with pytest.raises(TrackerRuntimeError, match="INVALID_PERSON_DETECTION"):
        tracker.update(frame(1, 0), [bad])
    assert tracker.update_failures == 1
    assert tracker.update_count == 0


def test_tracker_internal_failure_propagates() -> None:
    tracker = BoTSortTracker("camera-1", 5, 1000)

    class Broken:
        def update(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("private failure")

    tracker._tracker = Broken()
    with pytest.raises(TrackerRuntimeError, match="TRACKER_UPDATE_FAILED"):
        tracker.update(frame(1, 0), [box()])
    assert tracker.update_failures == 1


def test_track_state_lost_recovery_ttl_and_session_reset() -> None:
    class SequenceDetector:
        adapter_kind = "controlled"

        def detect(self, frames: object) -> list[list[BoundingBox]]:
            del frames
            return [[box()]] if calls.pop(0) else [[]]

    calls = [True, False, True, False, False]
    store = InMemoryTrackStore(ttl_ms=500)
    tracker = BoTSortTracker("camera-1", 5, 500)
    pipeline = RuntimePipeline(
        SequenceDetector(), tracker, store, BoundedFrameBuffer(2), FrameSampler(5),
        "worker", "rev",
    )
    first = pipeline.process_frame(frame(1, 0))
    assert first is not None and len(first.track_results) == 1
    key = TrackKey("camera-1", "session-1", first.track_results[0].track_id)
    state = store.get(key)
    assert state is not None and state.lifecycle is TrackLifecycle.ACTIVE
    assert state.last_detection_box is not None
    assert state.last_detection_box.x1 == 10
    assert state.last_detection_box.confidence == pytest.approx(0.9)

    pipeline.process_frame(frame(2, 200))
    assert state.lifecycle is TrackLifecycle.LOST
    assert state.last_seen_ms == 0
    recovered = pipeline.process_frame(frame(3, 400))
    assert recovered is not None and recovered.track_results[0].track_id == key.track_id
    assert state.lifecycle is TrackLifecycle.ACTIVE
    assert state.last_seen_ms == 400

    pipeline.process_frame(frame(4, 600))
    pipeline.process_frame(frame(5, 1000))
    assert store.get(key) is None
    status = pipeline.status(CameraConnectionState.CONNECTED, elapsed_seconds=1)
    assert status.expired_tracks == 1
    assert status.active_tracks == 0
    assert status.tracker_updates == 5
    assert status.tracker_failures == 0
    assert status.ring_buffer_frames == 2
    assert status.ring_buffer_overwrites == 3
    pipeline.close()
    assert store.active_count == 0


def test_new_stream_session_resets_tracker_and_track_state() -> None:
    store = InMemoryTrackStore(ttl_ms=5000)
    pipeline = RuntimePipeline(
        MockPersonDetector(detections=[box()]), BoTSortTracker("camera-1", 5, 5000),
        store, BoundedFrameBuffer(4), FrameSampler(5), "worker", "rev",
    )
    first = pipeline.process_frame(frame(1, 0))
    assert first is not None and first.track_results
    old_key = TrackKey("camera-1", "session-1", first.track_results[0].track_id)
    second = pipeline.process_frame(frame(2, 200, "session-2"))
    assert second is not None and second.track_results
    assert store.get(old_key) is None
    assert store.active_count == 1


def test_missing_tracker_dependency_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from vision_worker.tracking import bot_sort

    original = bot_sort.import_module

    def fail(name: str) -> object:
        if name == "trackers":
            raise ImportError("not installed")
        return original(name)

    monkeypatch.setattr(bot_sort, "import_module", fail)
    with pytest.raises(TrackerRuntimeError, match="TRACKER_DEPENDENCY_UNAVAILABLE"):
        BoTSortTracker("camera-1", 5, 1000)


def test_detector_failure_does_not_become_empty_tracker_update() -> None:
    class FailingDetector:
        adapter_kind = "yolo_onnx"

        def detect(self, frames: object) -> list[list[BoundingBox]]:
            del frames
            raise RuntimeError("MODEL_INFERENCE_ERROR")

    class CountingTracker:
        adapter_kind = "controlled"

        def __init__(self) -> None:
            self.calls = 0

        def update(self, current: VideoFrame, detections: object) -> list[object]:
            del current, detections
            self.calls += 1
            return []

        def reset(self, camera_id: str) -> None:
            del camera_id

    tracker = CountingTracker()
    pipeline = RuntimePipeline(
        FailingDetector(), tracker, InMemoryTrackStore(1000), BoundedFrameBuffer(2),
        FrameSampler(5), "worker", "rev",
    )
    with pytest.raises(RuntimeError, match="MODEL_INFERENCE_ERROR"):
        pipeline.process_frame(frame(1, 0))
    assert tracker.calls == 0
    assert pipeline.status(CameraConnectionState.ERROR).processed_frames == 0


def test_tracker_failure_does_not_report_processed_frame() -> None:
    class FailingTracker(BoTSortTracker):
        def update(self, current: VideoFrame, detections: object) -> list[object]:
            del current, detections
            raise TrackerRuntimeError("TRACKER_UPDATE_FAILED")

    pipeline = RuntimePipeline(
        MockPersonDetector(detections=[box()]), FailingTracker("camera-1", 5, 1000),
        InMemoryTrackStore(1000), BoundedFrameBuffer(2), FrameSampler(5), "worker", "rev",
    )
    with pytest.raises(TrackerRuntimeError, match="TRACKER_UPDATE_FAILED"):
        pipeline.process_frame(frame(1, 0))
    status = pipeline.status(CameraConnectionState.ERROR)
    assert status.processed_frames == 0
    assert status.tracker_failures == 1
    assert status.tracker_state == "ERROR"


def test_short_synthetic_run_remains_bounded() -> None:
    store = InMemoryTrackStore(ttl_ms=1000)
    buffer = BoundedFrameBuffer(4)
    pipeline = RuntimePipeline(
        MockPersonDetector(detections=[box()]), BoTSortTracker("camera-1", 5, 1000),
        store, buffer, FrameSampler(5), "worker", "rev",
    )
    for index in range(200):
        pipeline.process_frame(frame(index + 1, index * 200))
    status = pipeline.status(CameraConnectionState.CONNECTED, elapsed_seconds=40)
    assert status.received_frames == status.processed_frames == 200
    assert status.ring_buffer_frames == 4
    assert status.ring_buffer_overwrites == 196
    assert status.active_tracks == 1
    assert status.tracker_updates == 200
    pipeline.close()
    assert store.active_count == 0
    assert buffer.usage.frames == 0
