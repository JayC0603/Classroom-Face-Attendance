"""Controlled ONNX graphs exercise contracts, not real SCRFD/AdaFace accuracy."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from fr_config.models import FaceQualityConfig, TimingConfig
from fr_contracts.runtime_status import CameraConnectionState
from fr_domain.model_management import (
    DeploymentBackend,
    ModelManifest,
    ModelType,
    PreprocessingContract,
    TensorSpec,
)
from fr_domain.models import TrackKey
from onnx import TensorProto, helper, numpy_helper
from vision_worker.face_recognition.alignment import ARCFACE_112, FaceAligner, FaceAlignmentError
from vision_worker.face_recognition.embedding_pipeline import (
    FaceModelProvenance,
    FaceObservationStatus,
    FacePipelineError,
    TrackFaceProcessor,
)
from vision_worker.face_recognition.images import AlignedFace, FaceImageError, crop_person
from vision_worker.face_recognition.quality import FaceQualityEvaluator, FaceQualityStatus
from vision_worker.inference.adaface_onnx import AdaFaceContractError, AdaFaceOnnxEmbedder
from vision_worker.inference.interfaces import BoundingBox, FaceDetection, FaceLandmarks
from vision_worker.inference.onnx_session import OnnxInferenceSession
from vision_worker.inference.scrfd_onnx import ScrfdContractError, ScrfdOnnxDetector
from vision_worker.inference.stubs import MockPersonDetector
from vision_worker.model_delivery import (
    ModelDeploymentSynchronizer,
    PendingModelDeployment,
    PreparedAdaFaceEmbedder,
    PreparedScrfdDetector,
    ProductionAdapterFactory,
)
from vision_worker.model_runtime import RuntimeCapabilities
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore, TrackState
from vision_worker.tracking.bot_sort import BoTSortTracker
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


def scrfd_arrays() -> list[np.ndarray]:
    counts = [32, 8, 2]
    scores = [np.zeros((count, 1), dtype=np.float32) for count in counts]
    boxes = [np.zeros((count, 4), dtype=np.float32) for count in counts]
    landmarks = [np.zeros((count, 10), dtype=np.float32) for count in counts]
    scores[0][0, 0] = 0.95
    boxes[0][0] = [0, 0, 1, 1]
    landmarks[0][0] = [0.25, 0.25, 0.75, 0.25, 0.5, 0.5, 0.375, 0.75, 0.625, 0.75]
    return scores + boxes + landmarks


def scrfd_manifest() -> ModelManifest:
    names = [f"{part}_{stride}" for part in ("score", "bbox", "kps") for stride in (8, 16, 32)]
    return ModelManifest(
        model_type=ModelType.FACE_DETECTOR,
        inputs=[TensorSpec(name="input", shape=[1, 3, 32, 32], dtype="float32")],
        outputs=[
            TensorSpec(name=name, shape=list(array.shape), dtype="float32")
            for name, array in zip(names, scrfd_arrays(), strict=True)
        ],
        preprocessing=PreprocessingContract(
            layout="NCHW",
            color_order="RGB",
            normalization="scrfd_127_5_128",
            resize_mode="letterbox",
        ),
        metadata={
            "architecture": "SCRFD",
            "output_semantics": "scrfd_9_raw",
            "landmark_points": 5,
            "strides": [8, 16, 32],
            "anchor_count": 2,
            "confidence_threshold": 0.5,
            "iou_threshold": 0.4,
        },
        runtime_compatibility=[DeploymentBackend.ONNX_CPU],
    )


def adaface_manifest() -> ModelManifest:
    return ModelManifest(
        model_type=ModelType.FACE_EMBEDDER,
        inputs=[TensorSpec(name="input", shape=[1, 3, 112, 112], dtype="float32")],
        outputs=[TensorSpec(name="embedding", shape=[1, 4], dtype="float32")],
        preprocessing=PreprocessingContract(
            layout="NCHW",
            color_order="BGR",
            normalization="adaface_v1",
            resize_mode="aligned",
        ),
        metadata={
            "architecture": "AdaFace",
            "embedding_dimension": 4,
            "l2_normalized": True,
            "distance_metric": "cosine",
            "alignment_profile": "arcface_5point_112",
        },
        runtime_compatibility=[DeploymentBackend.ONNX_CPU],
    )


def constant_graph(
    path: Path, input_shape: list[int], outputs: list[tuple[str, np.ndarray]]
) -> str:
    nodes = [
        helper.make_node("ReduceMean", ["input"], ["mean"], keepdims=0),
        helper.make_node("Mul", ["mean", "zero"], ["offset"]),
    ]
    initializers = [numpy_helper.from_array(np.array(0, dtype=np.float32), "zero")]
    for name, value in outputs:
        initializers.append(numpy_helper.from_array(value, f"constant_{name}"))
        nodes.append(helper.make_node("Add", [f"constant_{name}", "offset"], [name]))
    graph = helper.make_graph(
        nodes,
        "controlled-face-graph",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, input_shape)],
        [
            helper.make_tensor_value_info(name, TensorProto.FLOAT, list(value.shape))
            for name, value in outputs
        ],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    payload = model.SerializeToString()
    path.write_bytes(payload)
    return sha256(payload).hexdigest()


def face_frame() -> VideoFrame:
    checker = ((np.indices((32, 32)).sum(axis=0) % 2) * 255).astype(np.uint8)
    pixels = np.repeat(checker[:, :, None], 3, axis=2)
    return VideoFrame(
        "camera",
        "session",
        1,
        timestamp_ms=100,
        width=32,
        height=32,
        pixel_format="RGB",
        payload=pixels.tobytes(),
    )


def scrfd_session(tmp_path: Path) -> tuple[OnnxInferenceSession, str, Path]:
    path = tmp_path / "controlled-scrfd.onnx"
    manifest = scrfd_manifest()
    checksum = constant_graph(
        path,
        [1, 3, 32, 32],
        [(item.name, array) for item, array in zip(manifest.outputs, scrfd_arrays(), strict=True)],
    )
    return OnnxInferenceSession(path, checksum, manifest), checksum, path


def adaface_session(tmp_path: Path) -> tuple[OnnxInferenceSession, str, Path]:
    path = tmp_path / "controlled-adaface.onnx"
    checksum = constant_graph(
        path,
        [1, 3, 112, 112],
        [("embedding", np.array([[3, 4, 0, 0]], dtype=np.float32))],
    )
    return OnnxInferenceSession(path, checksum, adaface_manifest()), checksum, path


def test_scrfd_controlled_onnx_preprocess_decode_landmarks_and_no_face(tmp_path: Path) -> None:
    session, _, _ = scrfd_session(tmp_path)
    detector = ScrfdOnnxDetector(session, scrfd_manifest())
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    tensor, scale = detector.preprocess(crop)
    assert tensor.shape == (1, 3, 32, 32) and tensor.dtype == np.float32
    assert scale == 1
    detector.warmup()
    faces = detector.detect([crop])[0]
    assert len(faces) == 1
    assert faces[0].box == BoundingBox(0, 0, 8, 8, pytest.approx(0.95))
    assert faces[0].landmarks is not None
    assert faces[0].landmarks.left_eye == (2, 2)
    assert faces[0].landmarks.right_mouth == (5, 6)
    outputs = scrfd_arrays()
    outputs[0][:] = 0
    assert detector.postprocess(outputs, 32, 32, 1) == []


def test_scrfd_nms_and_malformed_output_are_explicit(tmp_path: Path) -> None:
    session, _, _ = scrfd_session(tmp_path)
    detector = ScrfdOnnxDetector(session, scrfd_manifest())
    outputs = scrfd_arrays()
    outputs[0][1, 0] = 0.8
    outputs[3][1] = outputs[3][0]
    outputs[6][1] = outputs[6][0]
    assert len(detector.postprocess(outputs, 32, 32, 1)) == 1
    outputs[3][0, 0] = np.nan
    with pytest.raises(ScrfdContractError, match="INVALID_MODEL_OUTPUT"):
        detector.postprocess(outputs, 32, 32, 1)


def test_adaface_controlled_onnx_normalizes_and_rejects_invalid_output(tmp_path: Path) -> None:
    session, _, _ = adaface_session(tmp_path)
    embedder = AdaFaceOnnxEmbedder(session, adaface_manifest())
    aligned = AlignedFace(np.full((112, 112, 3), 128, dtype=np.uint8), 112, 112)
    tensor = embedder.preprocess(aligned)
    assert tensor.shape == (1, 3, 112, 112) and tensor.dtype == np.float32
    assert tensor[0, 0, 0, 0] == pytest.approx(128 / 127.5 - 1, abs=1e-6)
    embedder.warmup()
    assert embedder.embed([aligned])[0] == pytest.approx([0.6, 0.8, 0, 0])
    for invalid in (
        np.zeros((1, 4), dtype=np.float32),
        np.array([[np.nan, 1, 0, 0]], dtype=np.float32),
        np.array([[np.inf, 1, 0, 0]], dtype=np.float32),
        np.zeros((1, 3), dtype=np.float32),
    ):
        with pytest.raises(AdaFaceContractError, match="INVALID_EMBEDDING_OUTPUT"):
            embedder.postprocess(invalid)


def test_face_model_delivery_supports_roles_and_rejects_wrong_role(tmp_path: Path) -> None:
    _, scrfd_checksum, scrfd_path = scrfd_session(tmp_path)
    _, ada_checksum, ada_path = adaface_session(tmp_path)

    class Provider:
        def __init__(self, deployments: list[PendingModelDeployment]) -> None:
            self.deployments = deployments
            self.reports: list[dict[str, object]] = []

        def pending(self) -> list[PendingModelDeployment]:
            return self.deployments

        def report(self, deployment_id: str, **result: object) -> None:
            self.reports.append({"id": deployment_id, **result})

    deployments = [
        PendingModelDeployment(
            "face",
            ModelType.FACE_DETECTOR,
            DeploymentBackend.ONNX_CPU,
            scrfd_path,
            scrfd_checksum,
            "v1",
            "FP32",
            scrfd_manifest(),
            "r1",
        ),
        PendingModelDeployment(
            "embed",
            ModelType.FACE_EMBEDDER,
            DeploymentBackend.ONNX_CPU,
            ada_path,
            ada_checksum,
            "v1",
            "FP32",
            adaface_manifest(),
            "r1",
        ),
    ]
    provider = Provider(deployments)
    sync = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    assert sync.sync_once() == ["face", "embed"]
    assert isinstance(sync.active_models.active(ModelType.FACE_DETECTOR), PreparedScrfdDetector)
    assert isinstance(sync.active_models.active(ModelType.FACE_EMBEDDER), PreparedAdaFaceEmbedder)
    assert all(result["success"] is True for result in provider.reports)
    sync.active_models.close_all()

    wrong = deployments[0].__class__(
        "wrong",
        ModelType.FACE_EMBEDDER,
        DeploymentBackend.ONNX_CPU,
        scrfd_path,
        scrfd_checksum,
        "v1",
        "FP32",
        scrfd_manifest(),
        "r1",
    )
    provider = Provider([wrong])
    ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    ).sync_once()
    assert provider.reports[0]["error_category"] == "MODEL_TYPE_MISMATCH"


def test_quality_reasons_use_configured_thresholds() -> None:
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    accepted = FaceDetection(BoundingBox(0, 0, 8, 8, 0.95))
    config = FaceQualityConfig(
        min_face_size_px=4,
        min_detection_confidence=0.5,
        blur_threshold=10,
        brightness_min=20,
        brightness_max=230,
    )
    evaluator = FaceQualityEvaluator(config)
    result = evaluator.evaluate(crop, accepted)
    assert result.status is FaceQualityStatus.ACCEPTED
    assert result.pose_evaluated is False and result.occlusion_evaluated is False
    assert (
        evaluator.evaluate(crop, FaceDetection(BoundingBox(0, 0, 2, 2, 0.95))).status
        is FaceQualityStatus.FACE_TOO_SMALL
    )
    assert (
        evaluator.evaluate(crop, FaceDetection(BoundingBox(0, 0, 8, 8, 0.2))).status
        is FaceQualityStatus.LOW_DETECTION_CONFIDENCE
    )
    assert (
        evaluator.evaluate(crop, FaceDetection(BoundingBox(8, 8, 0, 0, 0.95))).status
        is FaceQualityStatus.INVALID_GEOMETRY
    )
    strict = FaceQualityEvaluator(config.model_copy(update={"min_face_size_px": 10}))
    assert strict.evaluate(crop, accepted).status is FaceQualityStatus.FACE_TOO_SMALL

    for intensity, expected in (
        (0, FaceQualityStatus.TOO_DARK),
        (255, FaceQualityStatus.TOO_BRIGHT),
        (128, FaceQualityStatus.TOO_BLURRY),
    ):
        flat = AlignedFace(np.full((32, 32, 3), intensity, dtype=np.uint8), 32, 32)
        flat_crop = crop.__class__(flat.pixels, crop.frame, 0, 0)
        assert evaluator.evaluate(flat_crop, accepted).status is expected


def test_alignment_canonical_translated_and_degenerate() -> None:
    pixels = np.repeat(np.arange(112, dtype=np.uint8)[None, :, None], 112, axis=0)
    pixels = np.repeat(pixels, 3, axis=2)
    frame = VideoFrame(
        "camera",
        "session",
        1,
        width=112,
        height=112,
        pixel_format="RGB",
        payload=pixels.tobytes(),
    )
    crop = crop_person(frame, BoundingBox(0, 0, 112, 112, 0.9))
    points = FaceLandmarks(*ARCFACE_112)
    aligner = FaceAligner()
    aligned = aligner.align(crop, points)
    assert aligned.pixels.shape == (112, 112, 3)
    assert aligned.width == aligned.height == 112
    shifted_pixels = np.pad(pixels, ((0, 8), (8, 0), (0, 0)))
    shifted_frame = VideoFrame(
        "camera",
        "session",
        2,
        width=120,
        height=120,
        pixel_format="RGB",
        payload=shifted_pixels.tobytes(),
    )
    shifted_crop = crop_person(shifted_frame, BoundingBox(0, 0, 120, 120, 0.9))
    shifted = FaceLandmarks(*tuple((x + 8, y) for x, y in ARCFACE_112))
    assert aligner.align(shifted_crop, shifted).pixels.shape == (112, 112, 3)
    scaled = FaceLandmarks(*tuple((x * 0.5, y * 0.5) for x, y in ARCFACE_112))
    assert aligner.align(crop, scaled).pixels.shape == (112, 112, 3)
    boundary_crop = crop_person(shifted_frame, BoundingBox(-10, -10, 60, 60, 0.9))
    assert (boundary_crop.left, boundary_crop.top, boundary_crop.width) == (0, 0, 60)
    with pytest.raises(FaceAlignmentError, match="INVALID_LANDMARK_GEOMETRY"):
        aligner.align(boundary_crop, points)
    with pytest.raises(FaceImageError, match="INVALID_PERSON_ROI"):
        crop_person(frame, BoundingBox(120, 120, 130, 130, 0.9))
    degenerate = FaceLandmarks((1, 1), (1, 1), (2, 2), (1, 3), (3, 3))
    with pytest.raises(FaceAlignmentError, match="INVALID_LANDMARK_GEOMETRY"):
        aligner.align(crop, degenerate)


def processor(detector: object, embedder: object) -> TrackFaceProcessor:
    return TrackFaceProcessor(
        detector,
        embedder,
        FaceModelProvenance(
            "scrfd-deploy",
            "CPUExecutionProvider",
            "ada-deploy",
            "CPUExecutionProvider",
            "ada-definition",
            "ada-version",
            "v1",
            4,
        ),
        FaceQualityConfig(
            min_face_size_px=4,
            min_detection_confidence=0.5,
            blur_threshold=10,
            brightness_min=20,
            brightness_max=230,
        ),
        TimingConfig(
            recognition_interval_ms=500,
            recognition_retry_ms=1000,
            track_cache_ttl_ms=5000,
        ),
    )


def test_controlled_onnx_track_to_face_embedding(tmp_path: Path) -> None:
    scrfd, _, _ = scrfd_session(tmp_path)
    adaface, _, _ = adaface_session(tmp_path)
    face_processor = processor(
        ScrfdOnnxDetector(scrfd, scrfd_manifest()),
        AdaFaceOnnxEmbedder(adaface, adaface_manifest()),
    )
    store = InMemoryTrackStore(5000)
    pipeline = RuntimePipeline(
        MockPersonDetector(detections=[BoundingBox(0, 0, 32, 32, 0.9)]),
        BoTSortTracker("camera", 5, 5000),
        store,
        BoundedFrameBuffer(3),
        FrameSampler(5),
        "worker",
        "revision",
        face_processor=face_processor,
    )
    result = pipeline.process_frame(face_frame())
    assert result is not None and len(result.track_results) == 1
    assert len(result.face_observations) == 1
    observation = result.face_observations[0]
    assert observation.status is FaceObservationStatus.EMBEDDED
    assert observation.track_id == result.track_results[0].track_id
    assert observation.camera_id == "camera" and observation.stream_session_id == "session"
    assert observation.face is not None and observation.face.box.x2 == 8
    assert observation.embedding is not None
    assert observation.embedding.values == pytest.approx((0.6, 0.8, 0, 0))
    assert observation.embedding.model_version_id == "ada-version"
    state = store.get(TrackKey("camera", "session", observation.track_id))
    assert state is not None and state.last_face_attempt_at_ms == 100
    assert state.last_valid_face_at_ms == 100
    assert not hasattr(state, "embedding")  # no unbounded vector retained
    status = pipeline.status(CameraConnectionState.CONNECTED)
    assert status.face_pipeline_runtime.embedding_successes == 1
    assert status.face_pipeline_runtime.state == "ACTIVE"
    assert status.face_pipeline_runtime.embedding_dimension == 4
    assert status.face_pipeline_runtime.latest_face_detection_ms is not None
    assert status.face_pipeline_runtime.latest_quality_ms is not None
    assert status.face_pipeline_runtime.latest_alignment_ms is not None
    assert status.face_pipeline_runtime.latest_embedding_ms is not None
    pipeline.close()
    scrfd.close()
    adaface.close()


def test_no_face_retry_and_embedding_failure_are_not_unknown() -> None:
    class NoFaces:
        adapter_kind = "controlled"

        def detect(self, crops: object) -> list[list[FaceDetection]]:
            del crops
            return [[]]

    class NeverEmbed:
        adapter_kind = "controlled"

        def embed(self, faces: object) -> list[list[float]]:
            del faces
            raise AssertionError("AdaFace must not run")

    face_processor = processor(NoFaces(), NeverEmbed())
    track = TrackedPerson(7, BoundingBox(0, 0, 32, 32, 0.9), 0.9)
    state = TrackState(TrackKey("camera", "session", 7), 100, 100)
    result = face_processor.process(face_frame(), track, state)
    assert result is not None and result.status is FaceObservationStatus.NO_FACE
    assert result.embedding is None
    assert face_processor.process(face_frame(), track, state) is None  # retry interval
    assert state.last_face_result == "NO_FACE"

    class InvalidEmbedding:
        adapter_kind = "controlled"

        def embed(self, faces: object) -> list[list[float]]:
            del faces
            return [[0, 0, 0, 0]]

    class OneFace:
        adapter_kind = "controlled"

        def detect(self, crops: object) -> list[list[FaceDetection]]:
            del crops
            return [
                [
                    FaceDetection(
                        BoundingBox(0, 0, 8, 8, 0.95),
                        FaceLandmarks((2, 2), (6, 2), (4, 4), (3, 6), (5, 6)),
                    )
                ]
            ]

    face_processor = processor(OneFace(), InvalidEmbedding())
    state = TrackState(TrackKey("camera", "session", 7), 100, 100)
    with pytest.raises(FacePipelineError, match="FACE_EMBEDDER_FAILED"):
        face_processor.process(face_frame(), track, state)
    assert face_processor.status().embedding_failures == 1
    assert state.last_face_result == "MODEL_FAILURE"


def test_quality_rejection_keeps_full_frame_coordinates_and_retries() -> None:
    class SmallFace:
        def detect(self, crops: object) -> list[list[FaceDetection]]:
            del crops
            return [[FaceDetection(BoundingBox(0, 0, 2, 2, 0.95))]]

    class NeverEmbed:
        def embed(self, faces: object) -> list[list[float]]:
            del faces
            raise AssertionError("Quality rejection must not invoke embedding")

    face_processor = processor(SmallFace(), NeverEmbed())
    track = TrackedPerson(7, BoundingBox(8, 8, 32, 32, 0.9), 0.9)
    state = TrackState(TrackKey("camera", "session", 7), 100, 100)
    first = face_processor.process(face_frame(), track, state)
    assert first is not None and first.status is FaceObservationStatus.QUALITY_REJECTED
    assert first.face is not None and first.face.box == BoundingBox(8, 8, 10, 10, 0.95)
    assert first.quality is not None and first.quality.status is FaceQualityStatus.FACE_TOO_SMALL
    assert face_processor.process(replace(face_frame(), timestamp_ms=1099), track, state) is None
    retried = face_processor.process(replace(face_frame(), timestamp_ms=1100), track, state)
    assert retried is not None and retried.status is FaceObservationStatus.QUALITY_REJECTED
    assert face_processor.status().quality_rejected == 2
    assert face_processor.status().quality_rejection_reasons == {"FACE_TOO_SMALL": 2}


def test_alignment_failure_is_not_embedded_and_detector_failure_is_technical() -> None:
    class BadLandmarks:
        def detect(self, crops: object) -> list[list[FaceDetection]]:
            del crops
            return [[FaceDetection(BoundingBox(0, 0, 8, 8, 0.95))]]

    class NeverEmbed:
        def embed(self, faces: object) -> list[list[float]]:
            del faces
            raise AssertionError("Alignment failure must not invoke embedding")

    track = TrackedPerson(7, BoundingBox(0, 0, 32, 32, 0.9), 0.9)
    state = TrackState(TrackKey("camera", "session", 7), 100, 100)
    face_processor = processor(BadLandmarks(), NeverEmbed())
    result = face_processor.process(face_frame(), track, state)
    assert result is not None and result.status is FaceObservationStatus.ALIGNMENT_FAILURE
    assert result.embedding is None
    assert face_processor.status().alignment_failures == 1

    class BrokenDetector:
        def detect(self, crops: object) -> list[list[FaceDetection]]:
            del crops
            raise RuntimeError("simulated ONNX provider error")

    face_processor = processor(BrokenDetector(), NeverEmbed())
    state = TrackState(TrackKey("camera", "session", 7), 100, 100)
    with pytest.raises(FacePipelineError, match="FACE_DETECTOR_FAILED"):
        face_processor.process(face_frame(), track, state)
    assert face_processor.status().face_detection_failures == 1
    assert face_processor.status().state == "ERROR"
    assert state.last_face_result == "MODEL_FAILURE"
