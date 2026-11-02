"""AdaFace-compatible FP32 ONNX embedding adapter; L2 normalization lives here."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from threading import local
from time import perf_counter
from typing import Any, Literal

from fr_domain.model_management import ModelManifest, ModelType

from vision_worker.face_recognition.images import AlignedFace
from vision_worker.inference.interfaces import ModelStageTimings
from vision_worker.inference.onnx_session import OnnxInferenceSession


class AdaFaceContractError(RuntimeError):
    pass


class AdaFaceOnnxEmbedder:
    adapter_kind = "adaface_onnx"

    def __init__(self, session: OnnxInferenceSession, manifest: ModelManifest) -> None:
        if manifest.model_type is not ModelType.FACE_EMBEDDER:
            raise AdaFaceContractError("MODEL_TYPE_MISMATCH")
        if str(manifest.metadata["architecture"]).upper() != "ADAFACE":
            raise AdaFaceContractError("UNSUPPORTED_MODEL_ARCHITECTURE")
        if manifest.metadata.get("alignment_profile") != "arcface_5point_112":
            raise AdaFaceContractError("UNSUPPORTED_ALIGNMENT_PROFILE")
        if manifest.metadata.get("l2_normalized") is not True:
            raise AdaFaceContractError("UNSUPPORTED_EMBEDDING_CONTRACT")
        if len(manifest.inputs) != 1 or len(manifest.outputs) != 1:
            raise AdaFaceContractError("INVALID_TENSOR_CONTRACT")
        pre = manifest.preprocessing
        if (
            pre.layout != "NCHW" or pre.color_order != "BGR"
            or pre.normalization != "adaface_v1" or pre.resize_mode != "aligned"
        ):
            raise AdaFaceContractError("UNSUPPORTED_PREPROCESSING")
        input_spec, output_spec = manifest.inputs[0], manifest.outputs[0]
        if input_spec.dtype != "float32" or input_spec.shape != [1, 3, 112, 112]:
            raise AdaFaceContractError("UNSUPPORTED_INPUT_CONTRACT")
        dimension = manifest.metadata["embedding_dimension"]
        if (
            isinstance(dimension, bool) or not isinstance(dimension, int)
            or dimension <= 0 or output_spec.dtype != "float32"
            or output_spec.shape != [1, dimension]
        ):
            raise AdaFaceContractError("INVALID_EMBEDDING_DIMENSION")
        self.embedding_dimension = dimension
        self._session = session
        self._np = import_module("numpy")
        self.attempts = 0
        self.failures = 0
        self.last_preprocess_ms: float | None = None
        self.last_inference_ms: float | None = None
        self.last_postprocess_ms: float | None = None
        self.last_total_ms: float | None = None
        self._thread_timings = local()

    def preprocess(self, aligned: AlignedFace) -> Any:
        pixels = self._np.asarray(aligned.pixels)
        if (
            aligned.width != 112 or aligned.height != 112
            or pixels.shape != (112, 112, 3) or pixels.dtype != self._np.uint8
        ):
            raise AdaFaceContractError("INVALID_ALIGNED_FACE")
        bgr = pixels[:, :, ::-1]
        return self._np.ascontiguousarray(
            (bgr.astype(self._np.float32) / 127.5 - 1).transpose(2, 0, 1)[None]
        )

    def postprocess(self, output: Any) -> list[float]:
        vector = self._np.asarray(output)
        if (
            vector.shape != (1, self.embedding_dimension)
            or vector.dtype != self._np.float32
            or not self._np.isfinite(vector).all()
        ):
            raise AdaFaceContractError("INVALID_EMBEDDING_OUTPUT")
        raw = vector[0].astype(self._np.float64)
        norm = float(self._np.linalg.norm(raw))
        if not self._np.isfinite(norm) or norm <= 1e-12:
            raise AdaFaceContractError("INVALID_EMBEDDING_OUTPUT")
        normalized = raw / norm
        return [float(value) for value in normalized]

    def embed(self, aligned_faces: Sequence[Any]) -> list[list[float]]:
        results: list[list[float]] = []
        for aligned in aligned_faces:
            started = perf_counter()
            self.attempts += 1
            self.last_preprocess_ms = self.last_inference_ms = self.last_postprocess_ms = None
            pre_ms = infer_ms = post_ms = None
            failed_stage: Literal["preprocessing", "inference", "postprocessing"] | None = None
            stage: Literal["preprocessing", "inference", "postprocessing"] = "preprocessing"
            try:
                pre = perf_counter()
                try:
                    if not isinstance(aligned, AlignedFace):
                        raise AdaFaceContractError("INVALID_ALIGNED_FACE")
                    tensor = self.preprocess(aligned)
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
                    if len(outputs) != 1:
                        raise AdaFaceContractError("INVALID_EMBEDDING_OUTPUT")
                    results.append(self.postprocess(outputs[0]))
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
        self.postprocess(self._session.warmup()[0])

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
