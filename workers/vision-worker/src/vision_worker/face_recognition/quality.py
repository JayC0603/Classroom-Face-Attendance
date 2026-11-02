"""Configured, deterministic face quality gates; pose/occlusion are not inferred."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from math import ceil, floor, isfinite

from fr_config.models import FaceQualityConfig

from vision_worker.face_recognition.images import PersonCrop
from vision_worker.inference.interfaces import FaceDetection


class FaceQualityStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    LOW_DETECTION_CONFIDENCE = "LOW_DETECTION_CONFIDENCE"
    TOO_DARK = "TOO_DARK"
    TOO_BRIGHT = "TOO_BRIGHT"
    TOO_BLURRY = "TOO_BLURRY"


@dataclass(frozen=True, slots=True)
class FaceQualityResult:
    status: FaceQualityStatus
    brightness: float | None = None
    laplacian_variance: float | None = None
    pose_evaluated: bool = False
    occlusion_evaluated: bool = False


class FaceQualityEvaluator:
    def __init__(self, config: FaceQualityConfig) -> None:
        self._config = config
        self._np = import_module("numpy")

    def evaluate(self, crop: PersonCrop, face: FaceDetection) -> FaceQualityResult:
        box = face.box
        if (
            not all(isfinite(value) for value in (box.x1, box.y1, box.x2, box.y2, box.confidence))
            or not 0 <= box.confidence <= 1
        ):
            return FaceQualityResult(FaceQualityStatus.INVALID_GEOMETRY)
        x1 = max(0, min(crop.width, floor(box.x1)))
        y1 = max(0, min(crop.height, floor(box.y1)))
        x2 = max(0, min(crop.width, ceil(box.x2)))
        y2 = max(0, min(crop.height, ceil(box.y2)))
        if box.x2 <= box.x1 or box.y2 <= box.y1 or x2 <= x1 or y2 <= y1:
            return FaceQualityResult(FaceQualityStatus.INVALID_GEOMETRY)
        width = min(float(crop.width), box.x2) - max(0.0, box.x1)
        height = min(float(crop.height), box.y2) - max(0.0, box.y1)
        if min(width, height) < self._config.min_face_size_px:
            return FaceQualityResult(FaceQualityStatus.FACE_TOO_SMALL)
        if box.confidence < self._config.min_detection_confidence:
            return FaceQualityResult(FaceQualityStatus.LOW_DETECTION_CONFIDENCE)
        pixels = crop.pixels[y1:y2, x1:x2]
        gray = (
            pixels[:, :, 0].astype(self._np.float32) * 0.299
            + pixels[:, :, 1].astype(self._np.float32) * 0.587
            + pixels[:, :, 2].astype(self._np.float32) * 0.114
        )
        brightness = float(gray.mean())
        if brightness < self._config.brightness_min:
            return FaceQualityResult(FaceQualityStatus.TOO_DARK, brightness)
        if brightness > self._config.brightness_max:
            return FaceQualityResult(FaceQualityStatus.TOO_BRIGHT, brightness)
        # Variance of a four-neighbour discrete Laplacian, not a universal quality score.
        laplacian = (
            gray[:-2, 1:-1] + gray[2:, 1:-1]
            + gray[1:-1, :-2] + gray[1:-1, 2:]
            - 4 * gray[1:-1, 1:-1]
        )
        sharpness = float(laplacian.var()) if laplacian.size else 0.0
        if sharpness < self._config.blur_threshold:
            return FaceQualityResult(FaceQualityStatus.TOO_BLURRY, brightness, sharpness)
        return FaceQualityResult(FaceQualityStatus.ACCEPTED, brightness, sharpness)
