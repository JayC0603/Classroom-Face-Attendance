"""Milestone 3 CPU contract tests; synthetic media and controlled ONNX only."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fr_config.models import FaceQualityConfig
from fr_domain.model_management import DeploymentBackend, ModelType
from fr_domain.models import TrackKey
from test_face_adapters import (
    adaface_manifest,
    adaface_session,
    face_frame,
    processor,
    scrfd_arrays,
    scrfd_manifest,
    scrfd_session,
)
from vision_worker.face_recognition.alignment import ARCFACE_112, FaceAligner, FaceAlignmentError
from vision_worker.face_recognition.embedding_pipeline import (
    FaceObservationStatus,
    FacePipelineError,
)
from vision_worker.face_recognition.images import (
    AlignedFace,
    FaceImageError,
    PersonCrop,
    crop_person,
)
from vision_worker.face_recognition.quality import FaceQualityEvaluator, FaceQualityStatus
from vision_worker.inference.adaface_onnx import AdaFaceContractError, AdaFaceOnnxEmbedder
from vision_worker.inference.interfaces import BoundingBox, FaceDetection, FaceLandmarks
from vision_worker.inference.scrfd_onnx import ScrfdContractError, ScrfdOnnxDetector
from vision_worker.model_delivery import (
    HttpDeploymentProvider,
    ModelDeploymentSynchronizer,
    PendingModelDeployment,
    ProductionAdapterFactory,
)
from vision_worker.model_runtime import AtomicModelSet, RuntimeCapabilities
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore, TrackState
from vision_worker.tracking.bot_sort import BoTSortTracker
from vision_worker.tracking.interfaces import TrackedPerson
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.sampling import FrameSampler


@pytest.fixture
def scrfd(tmp_path: Path):
    session, _, _ = scrfd_session(tmp_path)
    try:
        yield ScrfdOnnxDetector(session, scrfd_manifest())
    finally:
        session.close()


@pytest.fixture
def adaface(tmp_path: Path):
    session, _, _ = adaface_session(tmp_path)
    try:
        yield AdaFaceOnnxEmbedder(session, adaface_manifest())
    finally:
        session.close()


def state_and_track():
    track = TrackedPerson(7, BoundingBox(0, 0, 32, 32, 0.9), 0.9)
    return TrackState(TrackKey("camera", "session", 7), 100, 100), track


class Faces:
    def __init__(self, faces):
        self.faces = faces

    def detect(self, crops):
        return [self.faces for _ in crops]


class ValidEmbedding:
    def __init__(self):
        self.calls = 0

    def embed(self, faces):
        self.calls += 1
        return [[0.6, 0.8, 0, 0] for _ in faces]


def valid_face(confidence=0.95):
    return FaceDetection(
        BoundingBox(0, 0, 8, 8, confidence),
        FaceLandmarks((2, 2), (6, 2), (4, 4), (3, 6), (5, 6)),
    )


@pytest.mark.parametrize(
    "box",
    [
        BoundingBox(1.9, 1, 1.1, 5, 0.9),
        BoundingBox(1, 1.9, 5, 1.1, 0.9),
        BoundingBox(1.2, 1, 1.2, 5, 0.9),
    ],
)
def test_fractional_reversed_or_zero_area_person_roi_is_invalid(box):
    with pytest.raises(FaceImageError, match="INVALID_PERSON_ROI"):
        crop_person(face_frame(), box)


@pytest.mark.parametrize(
    "pixels",
    [
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8, 4), dtype=np.uint8),
        np.zeros((8, 8, 3), dtype=np.float32),
        np.zeros((0, 8, 3), dtype=np.uint8),
    ],
)
def test_scrfd_malformed_image_is_typed_failure(scrfd, pixels):
    crop = PersonCrop(pixels, face_frame(), 0, 0)
    with pytest.raises(ScrfdContractError, match="INVALID_PERSON_ROI"):
        scrfd.detect([crop])
    assert scrfd.failures == 1


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_scrfd_probability_contract_rejects_out_of_range_scores(scrfd, score):
    arrays = scrfd_arrays()
    arrays[0][0, 0] = score
    with pytest.raises(ScrfdContractError, match="INVALID_MODEL_OUTPUT"):
        scrfd.postprocess(arrays, 32, 32, 1)


def test_scrfd_negative_distance_is_invalid_output(scrfd):
    arrays = scrfd_arrays()
    arrays[3][0, 0] = -1
    with pytest.raises(ScrfdContractError, match="INVALID_MODEL_OUTPUT"):
        scrfd.postprocess(arrays, 32, 32, 1)


def test_scrfd_restores_actual_rounded_resize_axes(scrfd):
    pixels = np.zeros((31, 19, 3), dtype=np.uint8)
    tensor, scale = scrfd.preprocess(PersonCrop(pixels, face_frame(), 0, 0))
    assert tensor.shape == (1, 3, 32, 32)
    face = scrfd.postprocess(scrfd_arrays(), 19, 31, scale)[0]
    assert face.box.x2 == pytest.approx(8 * 19 / 20)
    assert face.box.y2 == pytest.approx(8 * 31 / 32)
    assert face.landmarks.left_eye == pytest.approx((2 * 19 / 20, 2 * 31 / 32))


def test_scrfd_padded_anchor_cannot_become_an_edge_face(scrfd):
    arrays = scrfd_arrays()
    arrays[0][:] = 0
    index = (1 * 4 + 3) * 2  # center (24,8); ROI fills only x=[0,16)
    arrays[0][index, 0] = 0.99
    arrays[3][index] = [2, 1, 1, 1]
    assert scrfd.postprocess(arrays, 16, 32, 1) == []


def test_quality_uses_actual_face_size_before_rounding():
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    quality = FaceQualityEvaluator(FaceQualityConfig(min_face_size_px=4))
    face = FaceDetection(BoundingBox(0.2, 0.2, 3.9, 3.9, 0.95))
    assert quality.evaluate(crop, face).status is FaceQualityStatus.FACE_TOO_SMALL


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_quality_rejects_invalid_confidence_geometry(confidence):
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    quality = FaceQualityEvaluator(FaceQualityConfig(min_face_size_px=4))
    assert (
        quality.evaluate(crop, valid_face(confidence)).status is FaceQualityStatus.INVALID_GEOMETRY
    )


def test_selection_discards_invalid_or_outside_candidates_before_ranking():
    invalid = [
        FaceDetection(BoundingBox(1, 1, 1, 5, 0.99)),
        FaceDetection(BoundingBox(-10, -10, 20, 20, 0.99)),
        FaceDetection(BoundingBox(float("nan"), 1, 5, 5, 0.99)),
    ]
    embedder = ValidEmbedding()
    face_processor = processor(Faces(invalid + [valid_face()]), embedder)
    state, track = state_and_track()
    result = face_processor.process(face_frame(), track, state)
    assert result.status is FaceObservationStatus.EMBEDDED
    assert result.face.box == valid_face().box
    assert embedder.calls == 1


def test_only_outside_faces_means_no_acceptable_candidate():
    face_processor = processor(
        Faces([FaceDetection(BoundingBox(-5, -5, 40, 40, 0.99))]), ValidEmbedding()
    )
    state, track = state_and_track()
    result = face_processor.process(face_frame(), track, state)
    assert result.status is FaceObservationStatus.NO_FACE


@pytest.mark.parametrize(
    "points",
    [
        ((1, 1), (3, 3), (4, 4), (5, 5), (7, 7)),
        ((2, 2), (6, 2), (3, 6), (3, 6), (5, 6)),
        ((2, 2), (6, 2), (float("nan"), 4), (3, 6), (5, 6)),
    ],
)
def test_alignment_rejects_collinear_duplicate_and_nonfinite_points(points):
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    with pytest.raises(FaceAlignmentError, match="INVALID_LANDMARK_GEOMETRY"):
        FaceAligner().align(crop, FaceLandmarks(*points))


@pytest.mark.parametrize(
    "pixels",
    [
        np.zeros((32, 32), dtype=np.uint8),
        np.zeros((32, 32, 3), dtype=np.float32),
    ],
)
def test_alignment_rejects_invalid_image_before_warp(pixels):
    crop = PersonCrop(pixels, face_frame(), 0, 0)
    with pytest.raises(FaceAlignmentError, match="INVALID_FACE_IMAGE"):
        FaceAligner().align(crop, valid_face().landmarks)


def test_missing_landmark_coordinate_is_alignment_failure():
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    points = FaceLandmarks((2, 2), (6, 2), (4,), (3, 6), (5, 6))
    with pytest.raises(FaceAlignmentError, match="INVALID_LANDMARK_GEOMETRY"):
        FaceAligner().align(crop, points)


def test_malformed_landmark_shape_is_an_alignment_observation():
    face = FaceDetection(valid_face().box, FaceLandmarks((2, 2), (6, 2), (4,), (3, 6), (5, 6)))
    face_processor = processor(Faces([face]), ValidEmbedding())
    state, track = state_and_track()
    result = face_processor.process(face_frame(), track, state)
    assert result.status is FaceObservationStatus.ALIGNMENT_FAILURE
    assert result.embedding is None


@pytest.mark.parametrize("scale,offset", [(1, (0, 0)), (1, (8, 5)), (0.5, (0, 0))])
def test_alignment_retains_transform_that_maps_source_to_canonical(scale, offset):
    pixels = np.zeros((128, 128, 3), dtype=np.uint8)
    crop = PersonCrop(pixels, face_frame(), 0, 0)
    source = np.asarray(ARCFACE_112) * scale + offset
    aligned = FaceAligner().align(crop, FaceLandmarks(*map(tuple, source)))
    transform = np.asarray(aligned.transform)
    mapped = np.column_stack((source, np.ones(5))) @ transform.T
    assert mapped == pytest.approx(np.asarray(ARCFACE_112), abs=1e-4)
    assert aligned.width == aligned.height == 112


def test_alignment_boundary_padding_is_valid_and_deterministic():
    crop = PersonCrop(np.full((16, 16, 3), 128, dtype=np.uint8), face_frame(), 0, 0)
    landmarks = FaceLandmarks(*tuple((x * 0.1, y * 0.1) for x, y in ARCFACE_112))
    aligned = FaceAligner().align(crop, landmarks)
    assert aligned.pixels.shape == (112, 112, 3)
    assert np.array_equal(aligned.pixels, FaceAligner().align(crop, landmarks).pixels)


def test_observation_has_alignment_metadata_and_latest_quality():
    face_processor = processor(Faces([valid_face()]), ValidEmbedding())
    state, track = state_and_track()
    result = face_processor.process(face_frame(), track, state)
    assert result.alignment.width == result.alignment.height == 112
    assert result.alignment.profile == "arcface_5point_112"
    assert len(result.alignment.transform) == 2
    assert state.last_face_quality_status == "ACCEPTED"
    assert "pixels" not in repr(result)
    assert "0.6" not in repr(result.embedding)  # vector values are not accidentally logged


def test_current_model_failure_replaces_previous_success_state_and_retries():
    class BrokenDetector:
        def detect(self, crops):
            raise RuntimeError("simulated failure")

    face_processor = processor(BrokenDetector(), ValidEmbedding())
    state, track = state_and_track()
    state.last_face_result = "EMBEDDED"
    with pytest.raises(FacePipelineError, match="FACE_DETECTOR_FAILED"):
        face_processor.process(face_frame(), track, state)
    assert state.last_face_result == "MODEL_FAILURE"
    assert face_processor.process(replace(face_frame(), timestamp_ms=1099), track, state) is None
    metrics = face_processor.status()
    assert metrics.failure_count == 1
    assert metrics.latest_error_category == "FACE_DETECTOR_FAILED"
    assert metrics.stage_latencies["scrfd"].failure_count == 1
    assert metrics.latest_face_detection_ms is not None


def test_recognition_interval_after_success_and_provenance():
    face_processor = processor(Faces([valid_face()]), ValidEmbedding())
    state, track = state_and_track()
    result = face_processor.process(face_frame(), track, state)
    assert face_processor.process(replace(face_frame(), timestamp_ms=599), track, state) is None
    later = face_processor.process(replace(face_frame(), timestamp_ms=600), track, state)
    assert later.status is FaceObservationStatus.EMBEDDED
    assert result.embedding.model_definition_id == "ada-definition"
    assert result.embedding.deployment_id == "ada-deploy"
    assert result.frame_timestamp_ms == 100 and result.frame_sequence == 1
    assert state.recognition_state.value == "PENDING" and state.verified_person_id is None


@pytest.mark.parametrize(
    "output",
    [
        np.ones(4, dtype=np.float32),
        np.ones((1, 1, 4), dtype=np.float32),
        np.ones((1, 4), dtype=np.float64),
        np.full((1, 4), 1e-20, dtype=np.float32),
    ],
)
def test_adaface_rejects_wrong_rank_dtype_and_near_zero(adaface, output):
    with pytest.raises(AdaFaceContractError, match="INVALID_EMBEDDING_OUTPUT"):
        adaface.postprocess(output)


def test_adaface_color_order_and_invalid_input(adaface):
    pixels = np.empty((112, 112, 3), dtype=np.uint8)
    pixels[:] = [0, 127, 255]
    tensor = adaface.preprocess(AlignedFace(pixels, 112, 112))
    assert tensor[0, :, 0, 0] == pytest.approx([1, 127 / 127.5 - 1, -1])
    with pytest.raises(AdaFaceContractError, match="INVALID_ALIGNED_FACE"):
        adaface.embed([AlignedFace(pixels.astype(np.float32), 112, 112)])


def test_closed_sessions_are_technical_failures_not_empty_results(tmp_path):
    session, _, _ = scrfd_session(tmp_path)
    detector = ScrfdOnnxDetector(session, scrfd_manifest())
    session.close()
    crop = crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
    from vision_worker.inference.onnx_session import OnnxSessionError

    with pytest.raises(OnnxSessionError, match="MODEL_CLOSED"):
        detector.detect([crop])
    session, _, _ = adaface_session(tmp_path)
    embedder = AdaFaceOnnxEmbedder(session, adaface_manifest())
    session.close()
    with pytest.raises(OnnxSessionError, match="MODEL_CLOSED"):
        embedder.embed([AlignedFace(np.zeros((112, 112, 3), np.uint8), 112, 112)])


def test_latency_statistics_are_bounded_and_preserve_failure_count():
    from vision_worker.face_recognition.latency import BoundedLatency

    window = BoundedLatency(capacity=3)
    for duration in [1, 2, 3, 4]:
        window.add(duration, failed=duration == 2)
    summary = window.summary()
    assert summary.sample_count == 3 and summary.attempt_count == 4
    assert summary.failure_count == 1
    assert summary.mean_ms == summary.median_ms == 3
    assert summary.p95_ms == 4  # nearest-rank p95
    empty = BoundedLatency().summary()
    assert empty.sample_count == 0 and empty.mean_ms is None
    with pytest.raises(ValueError):
        window.add(float("nan"))


def test_controlled_pipeline_reports_model_stage_timings_and_reuses_sessions(scrfd, adaface):
    face_processor = processor(scrfd, adaface)
    state, track = state_and_track()
    for timestamp in (100, 600, 1100):
        result = face_processor.process(replace(face_frame(), timestamp_ms=timestamp), track, state)
        assert result.status is FaceObservationStatus.EMBEDDED
    metrics = face_processor.status()
    for name in (
        "roi",
        "scrfd",
        "quality",
        "alignment",
        "adaface",
        "total",
        "scrfd_preprocessing",
        "scrfd_inference",
        "scrfd_postprocessing",
        "adaface_preprocessing",
        "adaface_inference",
        "adaface_postprocessing",
    ):
        summary = metrics.stage_latencies[name]
        assert summary.attempt_count == 3 and summary.failure_count == 0
        assert summary.mean_ms >= 0 and summary.median_ms >= 0 and summary.p95_ms >= 0
    assert metrics.embedding_successes == 3 and metrics.failure_count == 0
    assert metrics.latest_scrfd_inference_ms is not None
    assert metrics.latest_adaface_inference_ms is not None
    assert scrfd.attempts == adaface.attempts == 3
    assert scrfd.timings().inference_ms is not None
    assert adaface.timings().postprocessing_ms is not None
    # The factory-owned sessions remain live after repeat inference.
    assert scrfd.execution_provider == adaface.execution_provider == "CPUExecutionProvider"


@pytest.mark.parametrize("role", ["scrfd", "adaface"])
def test_shared_adapter_timings_belong_to_the_calling_camera_thread(scrfd, adaface, role):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    adapter = scrfd if role == "scrfd" else adaface
    invoke = adapter.detect if role == "scrfd" else adapter.embed
    valid = (
        crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))
        if role == "scrfd"
        else AlignedFace(np.zeros((112, 112, 3), np.uint8), 112, 112)
    )
    succeeded, failed = Event(), Event()

    def successful_camera():
        invoke([valid])
        succeeded.set()
        assert failed.wait(3)
        return adapter.timings()

    def failing_camera():
        assert succeeded.wait(3)
        with pytest.raises((ScrfdContractError, AdaFaceContractError)):
            invoke([None])
        failed.set()
        return adapter.timings()

    with ThreadPoolExecutor(max_workers=2) as pool:
        good = pool.submit(successful_camera)
        bad = pool.submit(failing_camera)
        assert bad.result().failed_stage == "preprocessing"
        assert good.result().failed_stage is None
        assert good.result().inference_ms is not None


def test_track_state_provenance_must_match_source_frame_and_track():
    face_processor = processor(Faces([valid_face()]), ValidEmbedding())
    state, track = state_and_track()
    state.key = TrackKey("another-camera", "session", 7)
    with pytest.raises(FacePipelineError, match="TRACK_PROVENANCE_MISMATCH"):
        face_processor.process(face_frame(), track, state)
    assert state.last_face_attempt_at_ms is None


@pytest.mark.parametrize("role", [ModelType.FACE_DETECTOR, ModelType.FACE_EMBEDDER])
def test_unassigned_activation_does_not_displace_assigned_face_runtime(tmp_path, role):
    make_session = scrfd_session if role is ModelType.FACE_DETECTOR else adaface_session
    session, checksum, path = make_session(tmp_path)
    session.close()
    manifest = scrfd_manifest() if role is ModelType.FACE_DETECTOR else adaface_manifest()
    old = PendingModelDeployment(
        "assigned",
        role,
        DeploymentBackend.ONNX_CPU,
        path,
        checksum,
        "v1",
        "FP32",
        manifest,
        "r1",
        "def",
        "ver",
    )
    candidate = replace(old, deployment_id="pending-unassigned")

    class AssignedProvider(HttpDeploymentProvider):
        pending_items = []
        results = []

        def assigned(self):
            return [old]

        def pending(self):
            return self.pending_items

        def report(self, deployment_id, **result):
            self.results.append({"id": deployment_id, **result})

    provider = AssignedProvider("http://controlled", "controlled-token")
    sync = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    try:
        sync.sync_once()
        active = sync.active_models.active(role)
        provider.pending_items = [candidate]
        sync.sync_once()
        assert sync.active_models.active(role) is active
        assert provider.results[-1]["success"] is True
        assert sync.active_models.active(role).deployment_id == "assigned"
    finally:
        sync.active_models.close_all()


@pytest.mark.parametrize("role", [ModelType.FACE_DETECTOR, ModelType.FACE_EMBEDDER])
def test_face_warmup_failure_retains_old_session_and_success_defers_retirement(tmp_path, role):
    make_session = scrfd_session if role is ModelType.FACE_DETECTOR else adaface_session
    session, checksum, path = make_session(tmp_path)
    session.close()
    manifest = scrfd_manifest() if role is ModelType.FACE_DETECTOR else adaface_manifest()
    old = PendingModelDeployment(
        "old", role, DeploymentBackend.ONNX_CPU, path, checksum, "v1", "FP32", manifest, "r1"
    )

    class Provider:
        items = [old]
        results = []

        def pending(self):
            return self.items

        def report(self, deployment_id, **result):
            self.results.append(result)

    provider = Provider()
    model_set = AtomicModelSet(defer_retirement=True)
    sync = ModelDeploymentSynchronizer(
        provider,
        RuntimeCapabilities.cpu_only(),
        ProductionAdapterFactory(),
        active_models=model_set,
    )
    try:
        sync.sync_once()
        active = model_set.active(role)
        from test_face_adapters import constant_graph

        if role is ModelType.FACE_DETECTOR:
            arrays = scrfd_arrays()
            arrays[3][0, 0] = np.nan
            outputs = [
                (spec.name, array) for spec, array in zip(manifest.outputs, arrays, strict=True)
            ]
            shape = [1, 3, 32, 32]
        else:
            outputs = [("embedding", np.zeros((1, 4), np.float32))]
            shape = [1, 3, 112, 112]
        bad_path = tmp_path / "bad-warmup.onnx"
        bad_checksum = constant_graph(bad_path, shape, outputs)
        provider.items = [
            replace(
                old, deployment_id="bad", artifact_path=bad_path, artifact_checksum=bad_checksum
            )
        ]
        sync.sync_once()
        assert model_set.active(role) is active
        assert provider.results[-1]["success"] is False
        provider.items = [replace(old, deployment_id="new")]
        sync.sync_once()
        assert model_set.active(role).deployment_id == "new"
        invoke = (
            (lambda: active.detect([crop_person(face_frame(), BoundingBox(0, 0, 32, 32, 0.9))]))
            if role is ModelType.FACE_DETECTOR
            else (lambda: active.embed([AlignedFace(np.zeros((112, 112, 3), np.uint8), 112, 112)]))
        )
        invoke()  # old reader can still use the session
        model_set.close_retired()
        from vision_worker.inference.onnx_session import OnnxSessionError

        with pytest.raises(OnnxSessionError, match="MODEL_CLOSED"):
            invoke()
    finally:
        model_set.close_all()


def test_controlled_deployment_yolo_bot_sort_scrfd_alignment_adaface_pipeline(tmp_path):
    from fr_config.models import TimingConfig
    from test_yolo_onnx import controlled_graph
    from test_yolo_onnx import manifest as yolo_manifest
    from vision_worker.face_recognition.embedding_pipeline import (
        FaceModelProvenance,
        TrackFaceProcessor,
    )

    path = tmp_path / "controlled-yolo.onnx"
    yolo_checksum = controlled_graph(path)
    scrfd_session_value, scrfd_checksum, scrfd_path = scrfd_session(tmp_path)
    ada_session_value, ada_checksum, ada_path = adaface_session(tmp_path)
    scrfd_session_value.close()
    ada_session_value.close()
    deployments = [
        PendingModelDeployment(
            "yolo",
            ModelType.PERSON_DETECTOR,
            DeploymentBackend.ONNX_CPU,
            path,
            yolo_checksum,
            "v1",
            "FP32",
            yolo_manifest(),
            "r1",
            "yd",
            "yv",
        ),
        PendingModelDeployment(
            "scrfd",
            ModelType.FACE_DETECTOR,
            DeploymentBackend.ONNX_CPU,
            scrfd_path,
            scrfd_checksum,
            "v1",
            "FP32",
            scrfd_manifest(),
            "r1",
            "sd",
            "sv",
        ),
        PendingModelDeployment(
            "adaface",
            ModelType.FACE_EMBEDDER,
            DeploymentBackend.ONNX_CPU,
            ada_path,
            ada_checksum,
            "v1",
            "FP32",
            adaface_manifest(),
            "r1",
            "ad",
            "av",
        ),
    ]

    class AssignedProvider(HttpDeploymentProvider):
        def assigned(self):
            return deployments

        def pending(self):
            return []

        def report(self, deployment_id, **result):
            assert result["success"]

    sync = ModelDeploymentSynchronizer(
        AssignedProvider("http://controlled", "test"),
        RuntimeCapabilities.cpu_only(),
        ProductionAdapterFactory(),
    )
    pipeline = None
    try:
        assert len(sync.sync_once()) == 3
        detector = sync.active_person_detector()
        face_detector = sync.active_face_detector()
        embedder = sync.active_face_embedder()
        face_processor = TrackFaceProcessor(
            face_detector,
            embedder,
            FaceModelProvenance(
                "scrfd",
                "CPUExecutionProvider",
                "adaface",
                "CPUExecutionProvider",
                "ad",
                "av",
                "v1",
                4,
            ),
            FaceQualityConfig(min_face_size_px=4, blur_threshold=10),
            TimingConfig(recognition_interval_ms=500, recognition_retry_ms=1000),
        )
        pipeline = RuntimePipeline(
            detector,
            BoTSortTracker("camera", 5, 10000),
            InMemoryTrackStore(),
            BoundedFrameBuffer(2),
            FrameSampler(5),
            "controlled-worker",
            "r1",
            face_processor=face_processor,
        )
        for index in range(50):
            result = pipeline.process_frame(
                replace(face_frame(), sequence=index + 1, timestamp_ms=index * 500)
            )
        assert len(result.person_detections) == len(result.track_results) == 1
        observation = result.face_observations[0]
        assert observation.status is FaceObservationStatus.EMBEDDED
        assert observation.face.box.x1 == 8 and observation.face.box.y1 == 8
        assert observation.face.landmarks.left_eye == (9, 9)
        assert observation.embedding.model_definition_id == "ad"
        assert observation.embedding.model_version_id == "av"
        assert observation.embedding.deployment_id == "adaface"
        assert np.linalg.norm(observation.embedding.values) == pytest.approx(1)
        assert observation.track_id == result.track_results[0].track_id
        assert observation.alignment.width == 112
        assert observation.alignment.source_origin == (8, 8)
        assert observation.alignment.coordinate_space == "PERSON_ROI"
        import json
        from importlib.metadata import version

        metrics = face_processor.status()
        assert metrics.embedding_successes == 50 and metrics.failure_count == 0
        baseline = {
            "status": "TESTED_WITH_CONTROLLED_TEST_MODEL",
            "production_benchmark": False,
            "iterations": 50,
            "provider": "CPUExecutionProvider",
            "scrfd_input": [1, 3, 32, 32],
            "adaface_input": [1, 3, 112, 112],
            "embedding_dimension": 4,
            "onnxruntime_version": version("onnxruntime"),
            "numpy_version": version("numpy"),
            "graphs": [
                {"filename": item.artifact_path.name, "sha256": item.artifact_checksum}
                for item in deployments
            ],
            "metrics": metrics.model_dump(),
        }
        (tmp_path / "controlled-face-cpu-baseline.json").write_text(
            json.dumps(baseline), encoding="utf-8"
        )
    finally:
        if pipeline is not None:
            pipeline.close()
        sync.active_models.close_all()


def test_face_model_error_degrades_worker_health():
    from fr_contracts.runtime_status import CameraConnectionState
    from vision_worker.inference.stubs import MockPersonDetector

    class BrokenDetector:
        def detect(self, crops):
            raise RuntimeError("injected failure")

    face_processor = processor(BrokenDetector(), ValidEmbedding())
    pipeline = RuntimePipeline(
        MockPersonDetector([BoundingBox(0, 0, 32, 32, 0.9)]),
        BoTSortTracker("camera", 5, 10000),
        InMemoryTrackStore(),
        BoundedFrameBuffer(2),
        FrameSampler(5),
        "controlled-worker",
        "r1",
        face_processor=face_processor,
    )
    try:
        result = pipeline.process_frame(face_frame())
        assert result.face_observations[0].status is FaceObservationStatus.MODEL_FAILURE
        assert result.face_observations[0].embedding is None
        status = pipeline.status(CameraConnectionState.CONNECTED)
        assert status.face_pipeline_runtime.state == "ERROR"
        assert status.worker_health == "unhealthy"
    finally:
        pipeline.close()


@pytest.mark.parametrize("mode", ["runner", "managed"])
def test_camera_runner_continues_after_model_failure_and_retries_without_unknown(mode):
    from threading import Event

    from test_runtime_runner import CapturingPublisher
    from vision_worker.app import VisionWorker
    from vision_worker.inference.stubs import MockPersonDetector
    from vision_worker.managed_runtime import ThreadedCameraRuntime
    from vision_worker.runtime.runner import RuntimeRunner
    from vision_worker.video.camera import GStreamerCameraSource

    class Backend:
        closed = Event()

        def open(self, uri, codec, transport):
            pass

        def read(self, camera_id, session_id, sequence):
            if sequence > 3:
                self.closed.wait(5)
                return None
            return replace(
                face_frame(),
                camera_id=camera_id,
                stream_session_id=session_id,
                sequence=sequence,
                timestamp_ms=(0, 200, 1200)[sequence - 1],
            )

        def close(self):
            self.closed.set()

    class TransientDetector:
        calls = 0

        def detect(self, crops):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient controlled provider failure")
            return [[valid_face()]]

    detector = TransientDetector()
    face_processor = processor(detector, ValidEmbedding())
    source = GStreamerCameraSource(
        "camera", "rtsp://controlled.invalid/stream", "H264", "tcp", backend=Backend()
    )
    pipeline = RuntimePipeline(
        MockPersonDetector([BoundingBox(0, 0, 32, 32, 0.9)]),
        BoTSortTracker("camera", 5, 10000),
        InMemoryTrackStore(),
        BoundedFrameBuffer(2),
        FrameSampler(5),
        "controlled",
        "r1",
        face_processor=face_processor,
    )

    class Publisher(CapturingPublisher):
        embedded = Event()

        def publish(self, status):
            super().publish(status)
            if status.face_pipeline_runtime.embedding_successes == 1:
                self.embedded.set()

    publisher = Publisher()
    managed = None
    try:
        if mode == "managed":
            managed = ThreadedCameraRuntime(source, VisionWorker(pipeline), publisher)
            assert publisher.embedded.wait(5), "Managed face retry never recovered"
            assert managed.error_category is None
            assert managed._thread.is_alive()
        else:
            summary = RuntimeRunner(source, pipeline, publisher).run(limit=3)
            assert summary.frames_received == 3
            assert summary.last_result.face_observations[0].status is FaceObservationStatus.EMBEDDED
        assert detector.calls == 2
        assert publisher.statuses[0].worker_health == "unhealthy"
        assert publisher.statuses[0].error_category == "MODEL_INFERENCE_ERROR"
        assert publisher.statuses[0].face_pipeline_runtime.face_detection_failures == 1
        assert publisher.statuses[-1].face_pipeline_runtime.embedding_successes == 1
        assert publisher.statuses[-1].error_category is None
    finally:
        if managed is not None:
            managed.close()
        else:
            source.close()
            pipeline.close()


@pytest.mark.parametrize("role", [ModelType.FACE_DETECTOR, ModelType.FACE_EMBEDDER])
def test_failed_http_assigned_replacement_keeps_last_valid_adapter_but_withdrawal_disables(
    tmp_path, role
):
    make_session = scrfd_session if role is ModelType.FACE_DETECTOR else adaface_session
    session, checksum, path = make_session(tmp_path)
    session.close()
    manifest = scrfd_manifest() if role is ModelType.FACE_DETECTOR else adaface_manifest()
    old = PendingModelDeployment(
        "old-assigned",
        role,
        DeploymentBackend.ONNX_CPU,
        path,
        checksum,
        "v1",
        "FP32",
        manifest,
        "r1",
    )

    class AssignedProvider(HttpDeploymentProvider):
        items = [old]
        results = []

        def assigned(self):
            return self.items

        def pending(self):
            return []

        def report(self, deployment_id, **result):
            self.results.append(result)

    provider = AssignedProvider("http://controlled", "test")
    sync = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    accessor = (
        sync.active_face_detector if role is ModelType.FACE_DETECTOR else sync.active_face_embedder
    )
    try:
        sync.sync_once()
        active = accessor()
        provider.items = [replace(old, deployment_id="replacement", artifact_checksum="0" * 64)]
        sync.sync_once()
        assert provider.results[-1]["error_category"] == "CHECKSUM_MISMATCH"
        assert accessor() is active
        provider.items = []
        sync.sync_once()
        assert accessor() is None  # withdrawn assignment never authorizes a stale adapter
    finally:
        sync.active_models.close_all()
