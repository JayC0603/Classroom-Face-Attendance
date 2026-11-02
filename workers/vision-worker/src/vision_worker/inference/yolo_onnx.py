from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from math import isfinite
from time import perf_counter
from typing import Any

from fr_domain.model_management import ModelManifest, ModelType

from vision_worker.inference.interfaces import BoundingBox
from vision_worker.inference.onnx_session import OnnxInferenceSession
from vision_worker.video.frame import VideoFrame


class YoloContractError(RuntimeError):
    pass


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    intersection = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1)) * max(
        0.0, min(a.y2, b.y2) - max(a.y1, b.y1)
    )
    area_a = (a.x2 - a.x1) * (a.y2 - a.y1)
    area_b = (b.x2 - b.x1) * (b.y2 - b.y1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def non_max_suppression(boxes: list[BoundingBox], threshold: float) -> list[BoundingBox]:
    kept: list[BoundingBox] = []
    for box in sorted(boxes, key=lambda item: item.confidence, reverse=True):
        if all(_iou(box, previous) <= threshold for previous in kept):
            kept.append(box)
    return kept


class YoloOnnxDetector:
    adapter_kind = "yolo_onnx"

    def __init__(self, session: OnnxInferenceSession, manifest: ModelManifest) -> None:
        self._np = import_module("numpy")
        self._image = import_module("PIL.Image")
        self._session = session
        self._manifest = manifest
        if manifest.model_type is not ModelType.PERSON_DETECTOR:
            raise YoloContractError("MODEL_TYPE_MISMATCH")
        architecture = str(manifest.metadata["architecture"]).upper()
        self._yolox = architecture == "YOLOX"
        if architecture not in {"YOLO", "YOLOX"}:
            raise YoloContractError("UNSUPPORTED_MODEL_ARCHITECTURE")
        semantics = manifest.metadata["output_semantics"]
        supported = (
            {"yolox_raw", "yolox_decoded", "yolox_xyxy"}
            if self._yolox
            else {"yolo_v8_raw", "yolo_v5_raw"}
        )
        if semantics not in supported:
            raise YoloContractError("UNSUPPORTED_OUTPUT_SEMANTICS")
        self._semantics = str(semantics)
        layout = manifest.metadata.get("output_layout", "BCN")
        if layout not in {"BCN", "BNC"}:
            raise YoloContractError("UNSUPPORTED_OUTPUT_LAYOUT")
        self._output_layout = layout
        if self._yolox and (layout != "BNC" or manifest.preprocessing.color_order != "BGR"):
            raise YoloContractError("UNSUPPORTED_PREPROCESSING")
        if len(manifest.inputs) != 1 or len(manifest.outputs) != 1:
            raise YoloContractError("INVALID_TENSOR_CONTRACT")
        spec = manifest.inputs[0]
        if spec.dtype != "float32" or manifest.preprocessing.layout != "NCHW":
            raise YoloContractError("UNSUPPORTED_INPUT_CONTRACT")
        if manifest.preprocessing.resize_mode != "letterbox":
            raise YoloContractError("UNSUPPORTED_PREPROCESSING")
        if manifest.preprocessing.normalization != ("raw_0_255" if self._yolox else "zero_to_one"):
            raise YoloContractError("UNSUPPORTED_PREPROCESSING")
        if (
            len(spec.shape) != 4
            or (spec.shape[0] != 1 and not (self._yolox and isinstance(spec.shape[0], str)))
            or spec.shape[1] != 3
        ):
            raise YoloContractError("UNSUPPORTED_INPUT_SHAPE")
        height, width = spec.shape[2:]
        if not isinstance(height, int) or not isinstance(width, int) or min(height, width) <= 0:
            raise YoloContractError("UNSUPPORTED_INPUT_SHAPE")
        self._height, self._width = height, width
        if manifest.outputs[0].dtype != "float32" or len(manifest.outputs[0].shape) != 3:
            raise YoloContractError("UNSUPPORTED_OUTPUT_CONTRACT")
        output_shape = manifest.outputs[0].shape
        if semantics == "yolox_xyxy" and output_shape[-1] != 6:
            raise YoloContractError("UNSUPPORTED_OUTPUT_CONTRACT")
        if output_shape[0] != 1 and not (self._yolox and isinstance(output_shape[0], str)):
            raise YoloContractError("UNSUPPORTED_OUTPUT_CONTRACT")
        first_class_column = 4 if self._semantics == "yolo_v8_raw" else 5
        channel_axis = 1 if layout == "BCN" else 2
        declared_channels = output_shape[channel_axis]
        class_ids = semantics == "yolox_xyxy"
        class_count = 1 if class_ids else manifest.metadata.get("class_count")
        if class_count is None and isinstance(declared_channels, int):
            class_count = declared_channels - first_class_column
        if isinstance(class_count, bool) or not isinstance(class_count, int) or class_count <= 0:
            raise YoloContractError("INVALID_CLASS_COUNT")
        if (
            not class_ids
            and isinstance(declared_channels, int)
            and declared_channels != first_class_column + class_count
        ):
            raise YoloContractError("INVALID_CLASS_COUNT")
        self._class_count = class_count
        self._grids: Any = None
        self._strides: Any = None
        if self._yolox:
            if max(height, width) > 2048 or height % 32 or width % 32:
                raise YoloContractError("UNSUPPORTED_INPUT_SHAPE")
            grids, strides = [], []
            for stride in (8, 16, 32):
                xx, yy = self._np.meshgrid(
                    self._np.arange(width // stride), self._np.arange(height // stride)
                )
                grid = self._np.stack((xx, yy), axis=-1).reshape(-1, 2)
                grids.append(grid)
                strides.append(self._np.full((len(grid), 1), stride))
            self._grids = self._np.concatenate(grids)
            self._strides = self._np.concatenate(strides)
            count = output_shape[1]
            if isinstance(count, int) and count != len(self._grids):
                raise YoloContractError("INVALID_YOLOX_GRID_SHAPE")
        person_class_id = manifest.metadata["person_class_id"]
        if (
            isinstance(person_class_id, bool)
            or not isinstance(person_class_id, int)
            or not 0 <= person_class_id < (1000 if class_ids else class_count)
        ):
            raise YoloContractError("INVALID_PERSON_CLASS_ID")
        self._person_class_id = person_class_id
        self._confidence = self._threshold("confidence_threshold", 0.25)
        self._iou_threshold = self._threshold("iou_threshold", 0.45)
        self.last_preprocess_ms: float | None = None
        self.last_postprocess_ms: float | None = None
        self.last_total_ms: float | None = None
        self.inference_failures = 0
        self.inference_count = 0
        self.total_latency_ms = 0.0
        self.last_attempt_failed = False

    def _threshold(self, name: str, default: float) -> float:
        value = self._manifest.metadata.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise YoloContractError("INVALID_DETECTION_THRESHOLD")
        numeric = float(value)
        if not isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise YoloContractError("INVALID_DETECTION_THRESHOLD")
        return numeric

    def preprocess(self, frame: VideoFrame) -> tuple[Any, float, int, int]:
        if frame.width <= 0 or frame.height <= 0 or frame.pixel_format != "RGB":
            raise YoloContractError("INVALID_FRAME_FORMAT")
        if (
            not isinstance(frame.payload, bytes)
            or len(frame.payload) != frame.width * frame.height * 3
        ):
            raise YoloContractError("INVALID_FRAME_SIZE")
        scale = min(self._width / frame.width, self._height / frame.height)
        if self._yolox:
            cv = import_module("cv2")
            pixels = self._np.frombuffer(frame.payload, dtype=self._np.uint8).reshape(
                frame.height, frame.width, 3
            )
            pixels = self._np.ascontiguousarray(pixels[:, :, ::-1])
            resized = cv.resize(
                pixels,
                (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
                interpolation=cv.INTER_LINEAR,
            )
            canvas = self._np.full((self._height, self._width, 3), 114, dtype=self._np.uint8)
            canvas[: resized.shape[0], : resized.shape[1]] = resized
            return (
                self._np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=self._np.float32),
                scale,
                0,
                0,
            )
        resized_width = min(self._width, max(1, round(frame.width * scale)))
        resized_height = min(self._height, max(1, round(frame.height * scale)))
        left = (self._width - resized_width) // 2
        top = (self._height - resized_height) // 2
        pixels = self._np.frombuffer(frame.payload, dtype=self._np.uint8).reshape(
            frame.height, frame.width, 3
        )
        image = self._image.fromarray(pixels).resize(
            (resized_width, resized_height), self._image.Resampling.BILINEAR
        )
        canvas = self._np.full((self._height, self._width, 3), 114, dtype=self._np.uint8)
        canvas[top : top + resized_height, left : left + resized_width] = self._np.asarray(image)
        if self._manifest.preprocessing.color_order == "BGR":
            canvas = canvas[:, :, ::-1]
        tensor = self._np.ascontiguousarray(
            canvas.transpose(2, 0, 1)[None].astype(self._np.float32) / 255.0
        )
        return tensor, scale, left, top

    def postprocess(
        self, output: Any, frame: VideoFrame, scale: float, left: int, top: int
    ) -> list[BoundingBox]:
        array = self._np.asarray(output)
        if array.ndim != 3 or array.shape[0] != 1 or not self._np.isfinite(array).all():
            raise YoloContractError("INVALID_MODEL_OUTPUT")
        first_class_column = 4 if self._semantics == "yolo_v8_raw" else 5
        rows = array[0].T if self._output_layout == "BCN" else array[0]
        if rows.shape[1] != first_class_column + self._class_count:
            raise YoloContractError("INVALID_MODEL_OUTPUT")
        if self._yolox:
            return self._postprocess_yolox(rows, frame, scale)
        boxes: list[BoundingBox] = []
        for row in rows:
            class_scores = row[first_class_column:]
            if int(self._np.argmax(class_scores)) != self._person_class_id:
                continue
            confidence = float(class_scores[self._person_class_id])
            if self._semantics == "yolo_v5_raw":
                confidence *= float(row[4])
            if confidence < self._confidence:
                continue
            cx, cy, width, height = map(float, row[:4])
            x1 = max(0.0, min(float(frame.width), (cx - width / 2 - left) / scale))
            y1 = max(0.0, min(float(frame.height), (cy - height / 2 - top) / scale))
            x2 = max(0.0, min(float(frame.width), (cx + width / 2 - left) / scale))
            y2 = max(0.0, min(float(frame.height), (cy + height / 2 - top) / scale))
            if x2 > x1 and y2 > y1:
                boxes.append(BoundingBox(x1, y1, x2, y2, confidence))
        return non_max_suppression(boxes, self._iou_threshold)

    def _postprocess_yolox(self, rows: Any, frame: VideoFrame, scale: float) -> list[BoundingBox]:
        if len(rows) != len(self._grids):
            raise YoloContractError("INVALID_YOLOX_GRID_SHAPE")
        xyxy = self._semantics == "yolox_xyxy"
        scores = rows[:, 4:5] if xyxy else rows[:, 4:]
        if self._np.any(scores < 0) or self._np.any(scores > 1):
            raise YoloContractError("INVALID_MODEL_SCORES")
        if xyxy:
            labels = rows[:, 5]
            if (
                self._np.any(labels < 0)
                or self._np.any(labels > 999)
                or self._np.any(labels != self._np.floor(labels))
            ):
                raise YoloContractError("INVALID_MODEL_CLASS_IDS")
            confidence = rows[:, 4]
            person_rows = labels == self._person_class_id
        else:
            confidence = rows[:, 4] * rows[:, 5 + self._person_class_id]
            person_rows = self._np.argmax(rows[:, 5:], axis=1) == self._person_class_id
        indexes = self._np.flatnonzero((confidence >= self._confidence) & person_rows)
        # Bound the quadratic NMS work even for a malformed/noisy high-score export.
        indexes = indexes[self._np.argsort(-confidence[indexes])[:300]]
        coordinates = rows[indexes, :4].astype(self._np.float64, copy=True)
        if self._semantics == "yolox_raw":
            if self._np.any(self._np.abs(coordinates[:, 2:]) > 30):
                raise YoloContractError("INVALID_MODEL_COORDINATES")
            coordinates[:, :2] = (coordinates[:, :2] + self._grids[indexes]) * self._strides[
                indexes
            ]
            coordinates[:, 2:] = self._np.exp(coordinates[:, 2:]) * self._strides[indexes]
        boxes = []
        for index, (cx, cy, width, height) in zip(indexes, coordinates, strict=True):
            edges = (
                (cx, cy, width, height)
                if xyxy
                else (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
            )
            x1 = max(0.0, min(float(frame.width), edges[0] / scale))
            y1 = max(0.0, min(float(frame.height), edges[1] / scale))
            x2 = max(0.0, min(float(frame.width), edges[2] / scale))
            y2 = max(0.0, min(float(frame.height), edges[3] / scale))
            if x2 > x1 and y2 > y1:
                boxes.append(
                    BoundingBox(
                        float(x1), float(y1), float(x2), float(y2), float(confidence[index])
                    )
                )
        return non_max_suppression(boxes, self._iou_threshold)[:25]

    def detect(self, frames: Sequence[VideoFrame]) -> list[list[BoundingBox]]:
        results: list[list[BoundingBox]] = []
        for frame in frames:
            started = perf_counter()
            try:
                pre = perf_counter()
                tensor, scale, left, top = self.preprocess(frame)
                self.last_preprocess_ms = (perf_counter() - pre) * 1000
                output = self._session.run({self._session.input_names[0]: tensor})[0]
                post = perf_counter()
                results.append(self.postprocess(output, frame, scale, left, top))
                self.last_postprocess_ms = (perf_counter() - post) * 1000
                self.inference_count += 1
                self.last_attempt_failed = False
            except Exception:
                self.inference_failures += 1
                self.last_attempt_failed = True
                raise
            finally:
                self.last_total_ms = (perf_counter() - started) * 1000
                self.total_latency_ms += self.last_total_ms
        return results

    def warmup(self) -> None:
        output = self._session.warmup()
        probe = VideoFrame(
            "warmup",
            "warmup",
            0,
            width=self._width,
            height=self._height,
            pixel_format="RGB",
            payload=bytes(self._width * self._height * 3),
        )
        self.postprocess(output[0], probe, 1.0, 0, 0)

    @property
    def warmed_up(self) -> bool:
        return self._session.warmup_ms is not None

    @property
    def execution_provider(self) -> str:
        return self._session.provider

    def close(self) -> None:
        self._session.close()
