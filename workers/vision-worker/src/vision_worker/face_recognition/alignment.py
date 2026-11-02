"""Five-point similarity alignment to the ArcFace/AdaFace 112-pixel profile."""

from __future__ import annotations

from importlib import import_module

from vision_worker.face_recognition.images import AlignedFace, PersonCrop
from vision_worker.inference.interfaces import FaceLandmarks


class FaceAlignmentError(RuntimeError):
    pass


# InsightFace's arcface_src template, used with AdaFace's aligned 112x112 input.
ARCFACE_112 = (
    (38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366),
    (41.5493, 92.3655), (70.7299, 92.2041),
)


class FaceAligner:
    def __init__(self, profile: str = "arcface_5point_112") -> None:
        if profile != "arcface_5point_112":
            raise FaceAlignmentError("UNSUPPORTED_ALIGNMENT_PROFILE")
        self.profile = profile
        self.target_size = 112
        self._np = import_module("numpy")
        self._cv2 = import_module("cv2")

    def align(self, crop: PersonCrop, landmarks: FaceLandmarks | None) -> AlignedFace:
        pixels = self._np.asarray(crop.pixels)
        if (
            pixels.ndim != 3 or pixels.shape[2] != 3
            or min(pixels.shape[:2]) < 2 or pixels.dtype != self._np.uint8
        ):
            raise FaceAlignmentError("INVALID_FACE_IMAGE")
        if not isinstance(landmarks, FaceLandmarks) or crop.width < 2 or crop.height < 2:
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        points = landmarks.points()
        try:
            source = self._np.asarray(points, dtype=self._np.float64)
        except (TypeError, ValueError, OverflowError) as error:
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY") from error
        if source.shape != (5, 2) or not self._np.isfinite(source).all():
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        if not all(0 <= x <= crop.width and 0 <= y <= crop.height for x, y in points):
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        if points[0] == points[1] or points[0][0] >= points[1][0]:
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        if points[3] == points[4] or points[3][0] >= points[4][0]:
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        if (
            self._np.unique(source, axis=0).shape[0] != 5
            or self._np.linalg.matrix_rank(source - source.mean(axis=0)) != 2
        ):
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        target = self._np.asarray(ARCFACE_112, dtype=self._np.float64)
        matrix = self._np.zeros((10, 4), dtype=self._np.float64)
        matrix[0::2, 0] = source[:, 0]
        matrix[0::2, 1] = -source[:, 1]
        matrix[0::2, 2] = 1
        matrix[1::2, 0] = source[:, 1]
        matrix[1::2, 1] = source[:, 0]
        matrix[1::2, 3] = 1
        parameters, _, rank, _ = self._np.linalg.lstsq(matrix, target.reshape(10), rcond=None)
        if rank != 4 or not self._np.isfinite(parameters).all():
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        a, b, tx, ty = (float(value) for value in parameters)
        if a * a + b * b < 1e-8:
            raise FaceAlignmentError("INVALID_LANDMARK_GEOMETRY")
        transform = self._np.asarray([[a, -b, tx], [b, a, ty]], dtype=self._np.float32)
        aligned = self._cv2.warpAffine(
            crop.pixels, transform, (self.target_size, self.target_size),
            flags=self._cv2.INTER_LINEAR, borderMode=self._cv2.BORDER_CONSTANT,
        )
        if aligned.shape != (112, 112, 3):
            raise FaceAlignmentError("ALIGNMENT_OUTPUT_INVALID")
        return AlignedFace(aligned, 112, 112, ((a, -b, tx), (b, a, ty)))
