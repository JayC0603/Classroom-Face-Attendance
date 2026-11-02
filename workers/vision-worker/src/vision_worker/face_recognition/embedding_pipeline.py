"""Track-scoped, transient face-to-embedding processing; no identity decision."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite, sqrt
from time import perf_counter
from typing import Literal

from fr_config.models import FaceQualityConfig, TimingConfig
from fr_contracts.runtime_status import FacePipelineRuntimeStatus
from fr_domain.models import TrackKey

from vision_worker.face_recognition.alignment import FaceAligner, FaceAlignmentError
from vision_worker.face_recognition.images import AffineTransform, FaceImageError, crop_person
from vision_worker.face_recognition.latency import BoundedLatency
from vision_worker.face_recognition.quality import (
    FaceQualityEvaluator,
    FaceQualityResult,
    FaceQualityStatus,
)
from vision_worker.inference.interfaces import (
    BoundingBox,
    FaceDetection,
    FaceDetector,
    FaceEmbedder,
    FaceLandmarks,
    InferenceTimingsProvider,
)
from vision_worker.runtime.track_store import TrackState
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.frame import VideoFrame


class FacePipelineError(RuntimeError):
    """Technical face-stage failure; never means NO_FACE or UNKNOWN."""


class FaceObservationStatus(StrEnum):
    EMBEDDED = "EMBEDDED"
    NO_FACE = "NO_FACE"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    ALIGNMENT_FAILURE = "ALIGNMENT_FAILURE"
    INVALID_PERSON_ROI = "INVALID_PERSON_ROI"
    MODEL_FAILURE = "MODEL_FAILURE"  # recorded in TrackState; technical errors still propagate


@dataclass(frozen=True, slots=True)
class FaceModelProvenance:
    face_detector_deployment_id: str
    face_detector_provider: str
    face_embedder_deployment_id: str
    face_embedder_provider: str
    embedder_definition_id: str
    embedder_version_id: str
    embedder_version: str
    embedding_dimension: int
    alignment_profile: str = "arcface_5point_112"


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    values: tuple[float, ...] = field(repr=False)
    dimension: int
    normalized: bool
    model_definition_id: str
    model_version_id: str
    model_version: str
    deployment_id: str
    alignment_profile: str
    generated_at: str


@dataclass(frozen=True, slots=True)
class FaceAlignmentMetadata:
    width: int
    height: int
    profile: str
    transform: AffineTransform | None
    source_origin: tuple[int, int]
    coordinate_space: Literal["PERSON_ROI"] = "PERSON_ROI"


@dataclass(frozen=True, slots=True)
class TrackFaceObservation:
    camera_id: str
    stream_session_id: str
    track_id: int
    frame_sequence: int
    frame_timestamp_ms: int
    status: FaceObservationStatus
    face: FaceDetection | None = None  # full-frame coordinates
    quality: FaceQualityResult | None = None
    embedding: EmbeddingVector | None = None
    alignment: FaceAlignmentMetadata | None = None


def _to_full_frame(face: FaceDetection, left: int, top: int) -> FaceDetection:
    box = face.box
    landmarks = face.landmarks
    full_landmarks = None
    if landmarks is not None:
        try:
            translated = tuple((x + left, y + top) for x, y in landmarks.points())
            if all(isfinite(value) for point in translated for value in point):
                full_landmarks = FaceLandmarks(*translated)
        except (TypeError, ValueError, OverflowError):
            # Alignment rejects the original malformed geometry. Do not fabricate
            # full-frame points or turn a geometry failure into an inference failure.
            pass
    return FaceDetection(
        BoundingBox(box.x1 + left, box.y1 + top, box.x2 + left, box.y2 + top, box.confidence),
        full_landmarks,
    )


class TrackFaceProcessor:
    def __init__(
        self,
        face_detector: FaceDetector,
        face_embedder: FaceEmbedder,
        provenance: FaceModelProvenance,
        quality: FaceQualityConfig,
        timing: TimingConfig,
    ) -> None:
        self._detector = face_detector
        self._embedder = face_embedder
        self._provenance = provenance
        self._quality = FaceQualityEvaluator(quality)
        self._aligner = FaceAligner(provenance.alignment_profile)
        self._timing = timing
        self._latencies: dict[str, BoundedLatency] = {}
        self._metrics = FacePipelineRuntimeStatus(
            state="ACTIVE",
            face_detector_deployment_id=provenance.face_detector_deployment_id,
            face_embedder_deployment_id=provenance.face_embedder_deployment_id,
            face_detector_provider=provenance.face_detector_provider,
            face_embedder_provider=provenance.face_embedder_provider,
            embedding_dimension=provenance.embedding_dimension,
        )

    def status(self) -> FacePipelineRuntimeStatus:
        snapshot = self._metrics.model_copy(deep=True)
        snapshot.stage_latencies = {
            name: window.summary() for name, window in self._latencies.items()
        }
        return snapshot

    def _record_latency(self, name: str, duration: float, failed: bool = False) -> None:
        if name not in self._latencies:
            self._latencies[name] = BoundedLatency()
        self._latencies[name].add(duration, failed=failed)

    @contextmanager
    def _measure(self, stage: str, latest_field: str) -> Iterator[None]:
        started = perf_counter()
        failed = False
        try:
            yield
        except Exception:
            failed = True
            raise
        finally:
            duration = (perf_counter() - started) * 1000
            setattr(self._metrics, latest_field, duration)
            self._record_latency(stage, duration, failed)

    def _record_model_timings(self, name: str, adapter: object) -> None:
        if not isinstance(adapter, InferenceTimingsProvider):
            return
        timings = adapter.timings()
        for stage, duration in (
            ("preprocessing", timings.preprocessing_ms),
            ("inference", timings.inference_ms),
            ("postprocessing", timings.postprocessing_ms),
        ):
            setattr(self._metrics, f"latest_{name}_{stage}_ms", duration)
            if duration is not None:
                self._record_latency(f"{name}_{stage}", duration, timings.failed_stage == stage)

    @staticmethod
    def _select_face(faces: list[FaceDetection], width: int, height: int) -> FaceDetection | None:
        candidates = [
            face
            for face in faces
            if all(
                isfinite(value)
                for value in (
                    face.box.x1,
                    face.box.y1,
                    face.box.x2,
                    face.box.y2,
                    face.box.confidence,
                )
            )
            and 0 <= face.box.confidence <= 1
            and 0 <= face.box.x1 < face.box.x2 <= width
            and 0 <= face.box.y1 < face.box.y2 <= height
        ]
        # Adapter boxes are clipped ROI-local. Require containment and positive geometry,
        # then confidence, area, left/top: deterministic and independent of identity.
        return min(
            candidates,
            key=lambda item: (
                -item.box.confidence,
                -(item.box.x2 - item.box.x1) * (item.box.y2 - item.box.y1),
                item.box.x1,
                item.box.y1,
            ),
            default=None,
        )

    def process(
        self, frame: VideoFrame, track: TrackedPerson, state: TrackState
    ) -> TrackFaceObservation | None:
        if state.key != TrackKey(frame.camera_id, frame.stream_session_id, track.track_id):
            raise FacePipelineError("TRACK_PROVENANCE_MISMATCH")
        previous = state.last_face_attempt_at_ms
        interval = (
            self._timing.recognition_retry_ms
            if state.last_face_result
            in {
                FaceObservationStatus.NO_FACE.value,
                FaceObservationStatus.QUALITY_REJECTED.value,
                FaceObservationStatus.ALIGNMENT_FAILURE.value,
                FaceObservationStatus.INVALID_PERSON_ROI.value,
                FaceObservationStatus.MODEL_FAILURE.value,
            }
            else self._timing.recognition_interval_ms
        )
        if previous is not None and frame.timestamp_ms - previous < interval:
            return None
        started = perf_counter()
        state.last_face_attempt_at_ms = frame.timestamp_ms
        state.last_face_quality_status = None
        self._metrics.latest_error_category = None
        # Latest timings refer only to this attempt, never a previous successful frame.
        for latest_field in (
            "latest_roi_ms",
            "latest_face_detection_ms",
            "latest_quality_ms",
            "latest_alignment_ms",
            "latest_embedding_ms",
            "latest_scrfd_preprocessing_ms",
            "latest_scrfd_inference_ms",
            "latest_scrfd_postprocessing_ms",
            "latest_adaface_preprocessing_ms",
            "latest_adaface_inference_ms",
            "latest_adaface_postprocessing_ms",
        ):
            setattr(self._metrics, latest_field, None)

        def observation(
            status: FaceObservationStatus,
            face: FaceDetection | None = None,
            quality: FaceQualityResult | None = None,
            embedding: EmbeddingVector | None = None,
            alignment: FaceAlignmentMetadata | None = None,
        ) -> TrackFaceObservation:
            state.last_face_result = status.value
            self._metrics.state = "ACTIVE"
            return TrackFaceObservation(
                frame.camera_id,
                frame.stream_session_id,
                track.track_id,
                frame.sequence,
                frame.timestamp_ms,
                status,
                face,
                quality,
                embedding,
                alignment,
            )

        try:
            try:
                with self._measure("roi", "latest_roi_ms"):
                    crop = crop_person(frame, track.box)
            except FaceImageError as error:
                if str(error) == "INVALID_PERSON_ROI":
                    return observation(FaceObservationStatus.INVALID_PERSON_ROI)
                self._metrics.state = "ERROR"
                raise FacePipelineError("FACE_CROP_FAILED") from error
            self._metrics.face_detection_attempts += 1
            try:
                with self._measure("scrfd", "latest_face_detection_ms"):
                    detected = self._detector.detect([crop])
                    if len(detected) != 1:
                        raise FacePipelineError("FACE_DETECTOR_OUTPUT_INVALID")
                    if any(not isinstance(face, FaceDetection) for face in detected[0]):
                        raise FacePipelineError("FACE_DETECTOR_OUTPUT_INVALID")
                    selected = self._select_face(detected[0], crop.width, crop.height)
            except Exception as error:
                self._metrics.face_detection_failures += 1
                self._metrics.state = "ERROR"
                raise FacePipelineError("FACE_DETECTOR_FAILED") from error
            finally:
                self._record_model_timings("scrfd", self._detector)
            if selected is None:
                self._metrics.no_face_count += 1
                return observation(FaceObservationStatus.NO_FACE)
            self._metrics.face_detection_successes += 1
            try:
                full_face = _to_full_frame(selected, crop.left, crop.top)
                with self._measure("quality", "latest_quality_ms"):
                    result = self._quality.evaluate(crop, selected)
            except Exception as error:
                self._metrics.state = "ERROR"
                raise FacePipelineError("FACE_OUTPUT_OR_QUALITY_FAILED") from error
            state.last_face_quality_status = result.status.value
            if result.status is not FaceQualityStatus.ACCEPTED:
                self._metrics.quality_rejected += 1
                reason = result.status.value
                counts = self._metrics.quality_rejection_reasons
                counts[reason] = counts.get(reason, 0) + 1
                return observation(FaceObservationStatus.QUALITY_REJECTED, full_face, result)
            self._metrics.quality_accepted += 1
            state.last_valid_face_at_ms = frame.timestamp_ms
            try:
                with self._measure("alignment", "latest_alignment_ms"):
                    aligned = self._aligner.align(crop, selected.landmarks)
            except FaceAlignmentError:
                self._metrics.alignment_failures += 1
                self._metrics.failure_count += 1
                return observation(FaceObservationStatus.ALIGNMENT_FAILURE, full_face, result)
            except Exception as error:
                self._metrics.alignment_failures += 1
                self._metrics.state = "ERROR"
                raise FacePipelineError("FACE_ALIGNMENT_FAILED") from error
            alignment = FaceAlignmentMetadata(
                aligned.width,
                aligned.height,
                self._aligner.profile,
                aligned.transform,
                (crop.left, crop.top),
            )
            self._metrics.embedding_attempts += 1
            try:
                with self._measure("adaface", "latest_embedding_ms"):
                    vectors = self._embedder.embed([aligned])
                    if len(vectors) != 1:
                        raise FacePipelineError("EMBEDDING_OUTPUT_INVALID")
                    values = tuple(vectors[0])
                    expected = self._provenance.embedding_dimension
                    norm = sqrt(sum(value * value for value in values))
                    if (
                        len(values) != expected
                        or not all(isfinite(value) for value in values)
                        or not isfinite(norm)
                        or abs(norm - 1.0) > 1e-3
                    ):
                        raise FacePipelineError("EMBEDDING_OUTPUT_INVALID")
            except Exception as error:
                self._metrics.embedding_failures += 1
                self._metrics.state = "ERROR"
                raise FacePipelineError("FACE_EMBEDDER_FAILED") from error
            finally:
                self._record_model_timings("adaface", self._embedder)
            self._metrics.embedding_successes += 1
            embedding = EmbeddingVector(
                values,
                expected,
                True,
                self._provenance.embedder_definition_id,
                self._provenance.embedder_version_id,
                self._provenance.embedder_version,
                self._provenance.face_embedder_deployment_id,
                self._provenance.alignment_profile,
                datetime.now(UTC).isoformat(),
            )
            return observation(
                FaceObservationStatus.EMBEDDED,
                full_face,
                result,
                embedding,
                alignment,
            )
        except FacePipelineError as error:
            state.last_face_result = FaceObservationStatus.MODEL_FAILURE.value
            self._metrics.failure_count += 1
            self._metrics.state = "ERROR"
            self._metrics.latest_error_category = str(error)
            raise
        finally:
            self._metrics.latest_total_latency_ms = (perf_counter() - started) * 1000
            self._record_latency(
                "total",
                self._metrics.latest_total_latency_ms,
                state.last_face_result in {"MODEL_FAILURE", "ALIGNMENT_FAILURE"},
            )
