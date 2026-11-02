"""ByteTrack adapter for YOLOX person detections."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from math import ceil, isfinite
from typing import Any

from vision_worker.inference.interfaces import BoundingBox
from vision_worker.tracking.bot_sort import TrackerRuntimeError
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.frame import VideoFrame


class ByteTrackPersonTracker:
    adapter_kind = "byte_track"

    def __init__(self, camera_id: str, processing_fps: float, track_cache_ttl_ms: int) -> None:
        if not camera_id or processing_fps <= 0 or track_cache_ttl_ms <= 0:
            raise ValueError("INVALID_TRACKER_CONFIGURATION")
        try:
            self._np = import_module("numpy")
            self._sv = import_module("supervision")
            tracker_module = import_module("trackers")
        except ImportError as error:
            raise TrackerRuntimeError("TRACKER_DEPENDENCY_UNAVAILABLE") from error
        self._camera_id = camera_id
        lost_buffer = max(1, ceil(track_cache_ttl_ms * processing_fps / 1000))
        try:
            self._tracker: Any = tracker_module.ByteTrackTracker(
                lost_track_buffer=lost_buffer,
                frame_rate=processing_fps,
                track_activation_threshold=0.25,
                minimum_consecutive_frames=2,
                minimum_iou_threshold=0.1,
                high_conf_det_threshold=0.5,
            )
        except Exception as error:
            raise TrackerRuntimeError("TRACKER_INITIALIZATION_FAILED") from error

    def update(self, frame: VideoFrame, detections: Sequence[BoundingBox]) -> list[TrackedPerson]:
        try:
            if frame.camera_id != self._camera_id:
                raise TrackerRuntimeError("TRACKER_CAMERA_MISMATCH")
            if frame.width <= 0 or frame.height <= 0:
                raise TrackerRuntimeError("INVALID_FRAME_GEOMETRY")
            for box in detections:
                values = (box.x1, box.y1, box.x2, box.y2, box.confidence)
                if not all(isfinite(v) for v in values) or not (
                    0 <= box.x1 < box.x2 <= frame.width
                    and 0 <= box.y1 < box.y2 <= frame.height
                    and 0 <= box.confidence <= 1
                ):
                    raise TrackerRuntimeError("INVALID_PERSON_DETECTION")
            supplied = self._sv.Detections(
                xyxy=self._np.asarray(
                    [(b.x1, b.y1, b.x2, b.y2) for b in detections], dtype=self._np.float32
                ).reshape(-1, 4),
                confidence=self._np.asarray(
                    [b.confidence for b in detections], dtype=self._np.float32
                ),
                class_id=self._np.zeros(len(detections), dtype=self._np.int32),
            )
            tracked = self._tracker.update(supplied, timestamp=frame.timestamp_ms / 1000)
            result = []
            for xyxy, score, track_id in zip(
                tracked.xyxy, tracked.confidence, tracked.tracker_id, strict=True
            ):
                track_id = int(track_id)
                if track_id < 0:
                    continue
                x1, y1, x2, y2 = (float(v) for v in xyxy)
                score = float(score)
                if not all(isfinite(v) for v in (x1, y1, x2, y2, score)):
                    raise TrackerRuntimeError("INVALID_TRACKER_OUTPUT")
                x1, x2 = min(max(x1, 0), frame.width), min(max(x2, 0), frame.width)
                y1, y2 = min(max(y1, 0), frame.height), min(max(y2, 0), frame.height)
                if x1 >= x2 or y1 >= y2 or not 0 <= score <= 1:
                    raise TrackerRuntimeError("INVALID_TRACKER_OUTPUT")
                result.append(TrackedPerson(track_id, BoundingBox(x1, y1, x2, y2, score), score))
            return result
        except TrackerRuntimeError:
            raise
        except Exception as error:
            raise TrackerRuntimeError("TRACKER_UPDATE_FAILED") from error

    def reset(self, camera_id: str) -> None:
        if camera_id != self._camera_id:
            raise TrackerRuntimeError("TRACKER_CAMERA_MISMATCH")
        try:
            self._tracker.reset()
        except Exception as error:
            raise TrackerRuntimeError("TRACKER_RESET_FAILED") from error
