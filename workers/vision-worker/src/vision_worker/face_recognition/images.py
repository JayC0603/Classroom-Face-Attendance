"""Transient RGB views; no face image is persisted by this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from math import ceil, floor, isfinite
from typing import Any

from vision_worker.inference.interfaces import BoundingBox
from vision_worker.video.frame import VideoFrame


class FaceImageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersonCrop:
    pixels: Any = field(repr=False)  # RGB view over the owning VideoFrame payload
    frame: VideoFrame
    left: int
    top: int

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])


AffineTransform = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True, slots=True)
class AlignedFace:
    pixels: Any = field(repr=False)  # owned RGB 112x112 image; transient
    width: int
    height: int
    transform: AffineTransform | None = None  # source ROI -> aligned face


def crop_person(frame: VideoFrame, box: BoundingBox) -> PersonCrop:
    if frame.pixel_format != "RGB" or frame.width <= 0 or frame.height <= 0:
        raise FaceImageError("INVALID_FRAME_FORMAT")
    if not isinstance(frame.payload, bytes) or len(frame.payload) != frame.width * frame.height * 3:
        raise FaceImageError("INVALID_FRAME_SIZE")
    if not all(isfinite(value) for value in (box.x1, box.y1, box.x2, box.y2)):
        raise FaceImageError("INVALID_PERSON_ROI")
    if box.x2 <= box.x1 or box.y2 <= box.y1:
        raise FaceImageError("INVALID_PERSON_ROI")
    left = max(0, min(frame.width, floor(box.x1)))
    top = max(0, min(frame.height, floor(box.y1)))
    right = max(0, min(frame.width, ceil(box.x2)))
    bottom = max(0, min(frame.height, ceil(box.y2)))
    if right <= left or bottom <= top:
        raise FaceImageError("INVALID_PERSON_ROI")
    np = import_module("numpy")
    full = np.frombuffer(frame.payload, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    return PersonCrop(full[top:bottom, left:right], frame, left, top)
