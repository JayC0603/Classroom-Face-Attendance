"""CPU BoT-SORT adapter around Roboflow Trackers; no ReID model or GMC."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from math import ceil, isfinite
from time import perf_counter
from typing import Any

from vision_worker.inference.interfaces import BoundingBox
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.frame import VideoFrame


class TrackerRuntimeError(RuntimeError):
    """Tracking input or execution failed; never represents zero people."""


class BoTSortTracker:
    adapter_kind = "bot_sort"
    reid_enabled = False
    gmc_enabled = False

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
        # The library scales its 30-FPS lost buffer to the configured processing FPS.
        lost_buffer = max(1, ceil(track_cache_ttl_ms * 30 / 1000))
        try:
            self._tracker: Any = tracker_module.BoTSORTTracker(
                lost_track_buffer=lost_buffer,
                frame_rate=processing_fps,
                enable_cmc=False,
                track_activation_threshold=0.5,
                high_conf_det_threshold=0.5,
            )
        except Exception as error:
            raise TrackerRuntimeError("TRACKER_INITIALIZATION_FAILED") from error
        self.update_count = 0
        self.update_failures = 0
        self.latest_latency_ms: float | None = None
        self._seen_ids: set[int] = set()
        self.created_tracks = 0

    @staticmethod
    def _validate(frame: VideoFrame, detections: Sequence[BoundingBox]) -> None:
        if frame.width <= 0 or frame.height <= 0:
            raise TrackerRuntimeError("INVALID_FRAME_GEOMETRY")
        for box in detections:
            values = (box.x1, box.y1, box.x2, box.y2, box.confidence)
            if not all(isfinite(value) for value in values):
                raise TrackerRuntimeError("INVALID_PERSON_DETECTION")
            if not (
                0 <= box.x1 < box.x2 <= frame.width
                and 0 <= box.y1 < box.y2 <= frame.height
                and 0 <= box.confidence <= 1
            ):
                raise TrackerRuntimeError("INVALID_PERSON_DETECTION")

    def update(self, frame: VideoFrame, detections: Sequence[BoundingBox]) -> list[TrackedPerson]:
        started = perf_counter()
        try:
            if frame.camera_id != self._camera_id:
                raise TrackerRuntimeError("TRACKER_CAMERA_MISMATCH")
            self._validate(frame, detections)
            boxes = self._np.asarray(
                [(box.x1, box.y1, box.x2, box.y2) for box in detections],
                dtype=self._np.float32,
            ).reshape(-1, 4)
            confidences = self._np.asarray(
                [box.confidence for box in detections], dtype=self._np.float32
            )
            supplied = self._sv.Detections(
                xyxy=boxes,
                confidence=confidences,
                class_id=self._np.zeros(len(detections), dtype=self._np.int32),
            )
            # GMC is disabled: passing the frame would copy RGB pixels for no benefit.
            tracked = self._tracker.update(supplied, timestamp=frame.timestamp_ms / 1000)
            output: list[TrackedPerson] = []
            for box, confidence, track_id in zip(
                tracked.xyxy, tracked.confidence, tracked.tracker_id, strict=True
            ):
                identity = int(track_id)
                if identity < 0:  # unconfirmed/low-confidence detection, not a stable track
                    continue
                coordinates = tuple(float(value) for value in box)
                score = float(confidence)
                if not all(isfinite(value) for value in (*coordinates, score)):
                    raise TrackerRuntimeError("INVALID_TRACKER_OUTPUT")
                x1 = min(max(coordinates[0], 0.0), float(frame.width))
                y1 = min(max(coordinates[1], 0.0), float(frame.height))
                x2 = min(max(coordinates[2], 0.0), float(frame.width))
                y2 = min(max(coordinates[3], 0.0), float(frame.height))
                if x1 >= x2 or y1 >= y2 or not 0 <= score <= 1:
                    raise TrackerRuntimeError("INVALID_TRACKER_OUTPUT")
                if identity not in self._seen_ids:
                    self._seen_ids.add(identity)
                    self.created_tracks += 1
                output.append(
                    TrackedPerson(
                        identity,
                        BoundingBox(x1, y1, x2, y2, score),
                        score,
                    )
                )
            self.update_count += 1
            return output
        except TrackerRuntimeError:
            self.update_failures += 1
            raise
        except Exception as error:
            self.update_failures += 1
            raise TrackerRuntimeError("TRACKER_UPDATE_FAILED") from error
        finally:
            self.latest_latency_ms = (perf_counter() - started) * 1000

    def reset(self, camera_id: str) -> None:
        if camera_id != self._camera_id:
            raise TrackerRuntimeError("TRACKER_CAMERA_MISMATCH")
        try:
            self._tracker.reset()
        except Exception as error:
            raise TrackerRuntimeError("TRACKER_RESET_FAILED") from error
        self._seen_ids.clear()
