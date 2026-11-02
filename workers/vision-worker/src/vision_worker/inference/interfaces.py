from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from vision_worker.video.frame import VideoFrame


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float


@dataclass(frozen=True, slots=True)
class FaceDetection:
    box: BoundingBox
    landmarks: FaceLandmarks | None = None


@dataclass(frozen=True, slots=True)
class FaceLandmarks:
    left_eye: tuple[float, float]
    right_eye: tuple[float, float]
    nose: tuple[float, float]
    left_mouth: tuple[float, float]
    right_mouth: tuple[float, float]

    def points(self) -> tuple[tuple[float, float], ...]:
        return (
            self.left_eye, self.right_eye, self.nose,
            self.left_mouth, self.right_mouth,
        )


class PersonDetector(Protocol):
    adapter_kind: str

    def detect(self, frames: Sequence[VideoFrame]) -> list[list[BoundingBox]]: ...


class FaceDetector(Protocol):
    adapter_kind: str

    def detect(self, person_crops: Sequence[Any]) -> list[list[FaceDetection]]: ...


class FaceEmbedder(Protocol):
    adapter_kind: str

    def embed(self, aligned_faces: Sequence[Any]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class ModelStageTimings:
    preprocessing_ms: float | None = None
    inference_ms: float | None = None
    postprocessing_ms: float | None = None
    failed_stage: Literal["preprocessing", "inference", "postprocessing"] | None = None


@runtime_checkable
class InferenceTimingsProvider(Protocol):
    """Optional adapter metrics; test adapters need only detect/embed."""

    def timings(self) -> ModelStageTimings: ...
