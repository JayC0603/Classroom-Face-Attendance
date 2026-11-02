from fr_contracts.runtime_status import CameraConnectionState
from fr_domain.models import TrackKey
from vision_worker.inference.interfaces import BoundingBox
from vision_worker.inference.stubs import MockPersonDetector
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore
from vision_worker.tracking.interfaces import MockTracker
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


def test_mock_detection_flows_through_tracking_and_track_state() -> None:
    detector = MockPersonDetector(detections=[BoundingBox(0.1, 0.1, 0.5, 0.9, confidence=0.95)])
    track_store = InMemoryTrackStore(ttl_ms=1000)
    pipeline = RuntimePipeline(
        person_detector=detector,
        tracker=MockTracker(),
        track_store=track_store,
        ring_buffer=BoundedFrameBuffer(capacity=4),
        sampler=FrameSampler(target_fps=10.0),
        worker_id="worker-01",
        active_config_revision="revision-7",
    )

    first = pipeline.process_frame(VideoFrame("camera-01", "session-01", 1, timestamp_ms=0))
    second = pipeline.process_frame(VideoFrame("camera-01", "session-01", 2, timestamp_ms=100))

    assert first is not None
    assert second is not None
    assert first.track_results[0].track_id == second.track_results[0].track_id
    assert track_store.active_count == 1

    status = pipeline.status(camera_state=CameraConnectionState.CONNECTED, elapsed_seconds=1.0)
    assert status.input_fps == 2.0
    assert status.processed_fps == 2.0
    assert status.dropped_frames == 0
    assert status.active_tracks == 1
    assert status.ring_buffer_frames == 2
    assert status.ring_buffer_capacity == 4
    assert status.worker_health == "degraded"  # mock adapters are not production health
    assert status.tracker_state == "UNAVAILABLE"
    assert status.active_config_revision == "revision-7"


def test_failed_camera_marks_worker_unhealthy() -> None:
    pipeline = RuntimePipeline(
        person_detector=MockPersonDetector(detections=[]),
        tracker=MockTracker(),
        track_store=InMemoryTrackStore(ttl_ms=1000),
        ring_buffer=BoundedFrameBuffer(capacity=2),
        sampler=FrameSampler(target_fps=5.0),
        worker_id="worker-01",
        active_config_revision=None,
    )

    status = pipeline.status(CameraConnectionState.FAILED, elapsed_seconds=1.0)

    assert status.worker_health == "unhealthy"


def test_pipeline_does_not_count_intentionally_sampled_out_frames_as_dropped() -> None:
    pipeline = RuntimePipeline(
        person_detector=MockPersonDetector(detections=[]),
        tracker=MockTracker(),
        track_store=InMemoryTrackStore(ttl_ms=1000),
        ring_buffer=BoundedFrameBuffer(capacity=2),
        sampler=FrameSampler(target_fps=5.0),
        worker_id="worker-01",
        active_config_revision=None,
    )

    pipeline.process_frame(VideoFrame("camera-01", "session-01", 1, timestamp_ms=0))
    result = pipeline.process_frame(VideoFrame("camera-01", "session-01", 2, timestamp_ms=100))

    assert result is None
    status = pipeline.status(
        camera_state=CameraConnectionState.CONNECTED,
        elapsed_seconds=1.0,
    )
    assert status.input_fps == 2.0
    assert status.processed_fps == 1.0
    assert status.dropped_frames == 0


def test_new_stream_session_clears_previous_camera_track_state() -> None:
    store = InMemoryTrackStore(ttl_ms=10_000)
    pipeline = RuntimePipeline(
        person_detector=MockPersonDetector(
            detections=[BoundingBox(0.1, 0.1, 0.5, 0.9, confidence=0.95)]
        ),
        tracker=MockTracker(),
        track_store=store,
        ring_buffer=BoundedFrameBuffer(capacity=4),
        sampler=FrameSampler(target_fps=10.0),
        worker_id="worker-01",
        active_config_revision="revision-7",
    )
    pipeline.process_frame(VideoFrame("camera-01", "session-old", 1, timestamp_ms=0))
    assert store.get(TrackKey("camera-01", "session-old", 1)) is not None

    pipeline.process_frame(VideoFrame("camera-01", "session-new", 2, timestamp_ms=100))

    assert store.get(TrackKey("camera-01", "session-old", 1)) is None
    assert store.active_count == 1
