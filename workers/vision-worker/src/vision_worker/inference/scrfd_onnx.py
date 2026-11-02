"""Nine-output SCRFD ONNX profile: scores, boxes, then five-point landmarks."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from math import isfinite
from threading import local
from time import perf_counter
from typing import Any, Literal

from fr_domain.model_management import ModelManifest, ModelType

from vision_worker.face_recognition.images import PersonCrop
from vision_worker.inference.interfaces import (
    BoundingBox,
    FaceDetection,
    FaceLandmarks,
    ModelStageTimings,
)
from vision_worker.inference.onnx_session import OnnxInferenceSession


class ScrfdContractError(RuntimeError):
    pass


def _overlap(first: BoundingBox, second: BoundingBox) -> float:
    width = max(0.0, min(first.x2, second.x2) - max(first.x1, second.x1))
    height = max(0.0, min(first.y2, second.y2) - max(first.y1, second.y1))
    intersection = width * height
    area_first = (first.x2 - first.x1) * (first.y2 - first.y1)
    area_second = (second.x2 - second.x1) * (second.y2 - second.y1)
    union = area_first + area_second - intersection
    return intersection / union if union > 0 else 0.0


class ScrfdOnnxDetector:
    adapter_kind = "scrfd_onnx"

    def __init__(self, session: OnnxInferenceSession, manifest: ModelManifest) -> None:
        if manifest.model_type is not ModelType.FACE_DETECTOR:
            raise ScrfdContractError("MODEL_TYPE_MISMATCH")
        meta = manifest.metadata
        if str(meta["architecture"]).upper() != "SCRFD":
            raise ScrfdContractError("UNSUPPORTED_MODEL_ARCHITECTURE")
        if meta["output_semantics"] != "scrfd_9_raw":
            raise ScrfdContractError("UNSUPPORTED_OUTPUT_SEMANTICS")
        if meta.get("strides") != [8, 16, 32] or meta.get("anchor_count") != 2:
            raise ScrfdContractError("UNSUPPORTED_SCRFD_PROFILE")
        if meta["landmark_points"] != 5:
            raise ScrfdContractError("INVALID_LANDMARK_CONTRACT")
        if len(manifest.inputs) != 1 or len(manifest.outputs) != 9:
            raise ScrfdContractError("INVALID_TENSOR_CONTRACT")
        spec = manifest.inputs[0]
        pre = manifest.preprocessing
        if (
            spec.dtype != "float32" or pre.layout != "NCHW"
            or pre.resize_mode != "letterbox"
            or pre.normalization != "scrfd_127_5_128"
        ):
            raise ScrfdContractError("UNSUPPORTED_PREPROCESSING")
        if len(spec.shape) != 4 or spec.shape[:2] != [1, 3]:
            raise ScrfdContractError("UNSUPPORTED_INPUT_SHAPE")
        height, width = spec.shape[2:]
        if (
            not isinstance(height, int) or not isinstance(width, int)
            or min(height, width) <= 0 or height % 32 or width % 32
        ):
            raise ScrfdContractError("UNSUPPORTED_INPUT_SHAPE")
        self._height, self._width = height, width
        self._color_order = pre.color_order
        for index, output in enumerate(manifest.outputs):
            if output.dtype != "float32" or len(output.shape) not in {2, 3}:
                raise ScrfdContractError("UNSUPPORTED_OUTPUT_CONTRACT")
            expected_last = 1 if index < 3 else 4 if index < 6 else 10
            if output.shape[-1] != expected_last:
                raise ScrfdContractError("UNSUPPORTED_OUTPUT_CONTRACT")
        self._confidence = self._threshold(meta.get("confidence_threshold", 0.5))
        self._nms = self._threshold(meta.get("iou_threshold", 0.4))
        self._session = session
        self._np = import_module("numpy")
        self._image = import_module("PIL.Image")
        self.attempts = 0
        self.failures = 0
        self.successes = 0
        self.no_face_count = 0
        self.last_preprocess_ms: float | None = None
        self.last_inference_ms: float | None = None
        self.last_postprocess_ms: float | None = None
        self.last_total_ms: float | None = None
        self._thread_timings = local()

    @staticmethod
    def _threshold(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScrfdContractError("INVALID_THRESHOLD")
        if not isfinite(value) or not 0 <= value <= 1:
            raise ScrfdContractError("INVALID_THRESHOLD")
        return float(value)

    def preprocess(self, crop: PersonCrop) -> tuple[Any, float]:
        pixels = self._np.asarray(crop.pixels)
        if (
            pixels.ndim != 3 or pixels.shape[2] != 3
            or min(pixels.shape[:2]) <= 0 or pixels.dtype != self._np.uint8
        ):
            raise ScrfdContractError("INVALID_PERSON_ROI")
        scale = min(self._width / crop.width, self._height / crop.height)
        resized_width = min(self._width, max(1, round(crop.width * scale)))
        resized_height = min(self._height, max(1, round(crop.height * scale)))
        resized = self._np.asarray(
            self._image.fromarray(crop.pixels).resize(
                (resized_width, resized_height), self._image.Resampling.BILINEAR
            )
        )
        canvas = self._np.zeros((self._height, self._width, 3), dtype=self._np.uint8)
        canvas[:resized_height, :resized_width] = resized
        if self._color_order == "BGR":
            canvas = canvas[:, :, ::-1]
        tensor = self._np.ascontiguousarray(
            ((canvas.astype(self._np.float32) - 127.5) / 128).transpose(2, 0, 1)[None]
        )
        return tensor, scale

    def _matrix(self, output: Any, rows: int, columns: int) -> Any:
        array = self._np.asarray(output)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        if (
            array.ndim != 2 or array.shape != (rows, columns)
            or array.dtype != self._np.float32 or not self._np.isfinite(array).all()
        ):
            raise ScrfdContractError("INVALID_MODEL_OUTPUT")
        return array

    def postprocess(
        self, outputs: Sequence[Any], crop_width: int, crop_height: int, scale: float
    ) -> list[FaceDetection]:
        if (
            len(outputs) != 9 or not isfinite(scale) or scale <= 0
            or crop_width <= 0 or crop_height <= 0
        ):
            raise ScrfdContractError("INVALID_MODEL_OUTPUT")
        resized_width = min(self._width, max(1, round(crop_width * scale)))
        resized_height = min(self._height, max(1, round(crop_height * scale)))
        scale_x, scale_y = resized_width / crop_width, resized_height / crop_height
        candidates: list[FaceDetection] = []
        for level, stride in enumerate((8, 16, 32)):
            grid_width, grid_height = self._width // stride, self._height // stride
            count = grid_width * grid_height * 2
            scores = self._matrix(outputs[level], count, 1)
            boxes = self._matrix(outputs[level + 3], count, 4)
            landmarks = self._matrix(outputs[level + 6], count, 10)
            if (
                self._np.any(scores < 0) or self._np.any(scores > 1)
                or self._np.any(boxes < 0)
            ):
                raise ScrfdContractError("INVALID_MODEL_OUTPUT")
            selected = self._np.flatnonzero(scores[:, 0] >= self._confidence)
            # Bound NMS work under pathological but shape-valid outputs.
            selected = selected[self._np.argsort(scores[selected, 0])[::-1][:300]]
            for index in selected:
                cell = int(index) // 2
                center_x = (cell % grid_width) * stride
                center_y = (cell // grid_width) * stride
                # Anchors in letterbox padding do not belong to the source ROI.
                if center_x >= resized_width or center_y >= resized_height:
                    continue
                left, top, right, bottom = (float(value) * stride for value in boxes[index])
                x1 = max(0.0, min(float(crop_width), (center_x - left) / scale_x))
                y1 = max(0.0, min(float(crop_height), (center_y - top) / scale_y))
                x2 = max(0.0, min(float(crop_width), (center_x + right) / scale_x))
                y2 = max(0.0, min(float(crop_height), (center_y + bottom) / scale_y))
                if x2 <= x1 or y2 <= y1:
                    continue
                points = tuple(
                    (
                        (center_x + float(landmarks[index, point * 2]) * stride) / scale_x,
                        (center_y + float(landmarks[index, point * 2 + 1]) * stride) / scale_y,
                    )
                    for point in range(5)
                )
                candidates.append(
                    FaceDetection(
                        BoundingBox(x1, y1, x2, y2, float(scores[index, 0])),
                        FaceLandmarks(*points),
                    )
                )
        kept: list[FaceDetection] = []
        for face in sorted(candidates, key=lambda item: item.box.confidence, reverse=True):
            if all(_overlap(face.box, previous.box) <= self._nms for previous in kept):
                kept.append(face)
        return kept

    def detect(self, person_crops: Sequence[Any]) -> list[list[FaceDetection]]:
        results: list[list[FaceDetection]] = []
        for crop in person_crops:
            started = perf_counter()
            self.attempts += 1
            self.last_preprocess_ms = self.last_inference_ms = self.last_postprocess_ms = None
            pre_ms = infer_ms = post_ms = None
            failed_stage: Literal["preprocessing", "inference", "postprocessing"] | None = None
            stage: Literal["preprocessing", "inference", "postprocessing"] = "preprocessing"
            try:
                pre = perf_counter()
                try:
                    if not isinstance(crop, PersonCrop):
                        raise ScrfdContractError("INVALID_PERSON_ROI")
                    tensor, scale = self.preprocess(crop)
                finally:
                    pre_ms = (perf_counter() - pre) * 1000
                stage = "inference"
                infer = perf_counter()
                try:
                    outputs = self._session.run({self._session.input_names[0]: tensor})
                finally:
                    infer_ms = (perf_counter() - infer) * 1000
                stage = "postprocessing"
                post = perf_counter()
                try:
                    faces = self.postprocess(outputs, crop.width, crop.height, scale)
                    results.append(faces)
                    self.successes += 1
                    self.no_face_count += int(not faces)
                finally:
                    post_ms = (perf_counter() - post) * 1000
            except Exception:
                self.failures += 1
                failed_stage = stage
                raise
            finally:
                self.last_preprocess_ms, self.last_inference_ms = pre_ms, infer_ms
                self.last_postprocess_ms = post_ms
                self._thread_timings.value = ModelStageTimings(
                    pre_ms, infer_ms, post_ms, failed_stage,
                )
                self.last_total_ms = (perf_counter() - started) * 1000
        return results

    def warmup(self) -> None:
        self.postprocess(self._session.warmup(), self._width, self._height, 1.0)

    def timings(self) -> ModelStageTimings:
        value = getattr(self._thread_timings, "value", None)
        return value if isinstance(value, ModelStageTimings) else ModelStageTimings()

    @property
    def warmed_up(self) -> bool:
        return self._session.warmup_ms is not None

    @property
    def execution_provider(self) -> str:
        return self._session.provider

    def close(self) -> None:
        self._session.close()
