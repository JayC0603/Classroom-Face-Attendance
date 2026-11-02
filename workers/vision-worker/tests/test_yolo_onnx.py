from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from fr_config.models import FaceRecognitionConfig
from fr_contracts.runtime_status import CameraConnectionState
from fr_domain.model_management import (
    DeploymentBackend,
    ModelManifest,
    ModelType,
    PreprocessingContract,
    TensorSpec,
)
from onnx import TensorProto, helper, numpy_helper
from vision_worker.app import VisionWorker
from vision_worker.inference.onnx_session import OnnxInferenceSession, OnnxSessionError
from vision_worker.inference.yolo_onnx import YoloContractError, YoloOnnxDetector
from vision_worker.model_delivery import (
    HttpDeploymentProvider,
    ModelDeploymentSynchronizer,
    PendingModelDeployment,
    PreparedYoloDetector,
    ProductionAdapterFactory,
)
from vision_worker.model_runtime import RuntimeCapabilities
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore
from vision_worker.tracking.bot_sort import BoTSortTracker
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler


def manifest(**metadata: object) -> ModelManifest:
    return ModelManifest(
        model_type=ModelType.PERSON_DETECTOR,
        inputs=[TensorSpec(name="images", shape=[1, 3, 8, 8], dtype="float32")],
        outputs=[TensorSpec(name="detections", shape=[1, 6, 2], dtype="float32")],
        preprocessing=PreprocessingContract(
            layout="NCHW", color_order="RGB", normalization="zero_to_one", resize_mode="letterbox"
        ),
        metadata={
            "architecture": "YOLO",
            "output_semantics": "yolo_v8_raw",
            "output_layout": "BCN",
            "person_class_id": 0,
            "confidence_threshold": 0.25,
            "iou_threshold": 0.45,
            **metadata,
        },
        runtime_compatibility=[DeploymentBackend.ONNX_CPU],
    )


def controlled_graph(path: Path) -> str:
    # This executes a real ONNX graph but is deliberately NOT a YOLO model.
    values = np.array(
        [[[4, 4], [4, 4], [4, 4], [4, 4], [0.9, 0.8], [0.1, 0.2]]],
        dtype=np.float32,
    )
    graph = helper.make_graph(
        [
            helper.make_node("ReduceMean", ["images"], ["mean"], keepdims=0),
            helper.make_node("Mul", ["mean", "zero"], ["offset"]),
            helper.make_node("Add", ["constant", "offset"], ["detections"]),
        ],
        "controlled-detector-graph",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("detections", TensorProto.FLOAT, [1, 6, 2])],
        [
            numpy_helper.from_array(values, "constant"),
            numpy_helper.from_array(np.array(0, dtype=np.float32), "zero"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    payload = model.SerializeToString()
    path.write_bytes(payload)
    return sha256(payload).hexdigest()


def frame() -> VideoFrame:
    return VideoFrame(
        "camera", "session", 1, width=8, height=4, pixel_format="RGB", payload=bytes([128] * 96)
    )


def test_cpu_session_and_yolo_adapter_execute_controlled_graph(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    session = OnnxInferenceSession(path, checksum, manifest())
    assert session.provider == "CPUExecutionProvider"
    assert session.input_names == ("images",)
    assert session.output_names == ("detections",)
    detector = YoloOnnxDetector(session, manifest())
    detector.warmup()
    assert session.warmup_ms is not None
    tensor, scale, left, top = detector.preprocess(frame())
    assert tensor.shape == (1, 3, 8, 8)
    assert tensor.dtype == np.float32
    assert (scale, left, top) == (1.0, 0, 2)
    assert np.allclose(tensor[0, :, 0, 0], 114 / 255)
    found = detector.detect([frame()])
    assert len(found) == 1 and len(found[0]) == 1  # overlapping candidates undergo NMS
    box = found[0][0]
    assert (box.x1, box.y1, box.x2, box.y2) == (2.0, 0.0, 6.0, 4.0)
    assert box.confidence == pytest.approx(0.9)
    assert session.last_inference_ms is not None
    detector.close()
    with pytest.raises(OnnxSessionError, match="MODEL_CLOSED"):
        session.run({"images": tensor})


def test_invalid_artifact_and_tensor_contract_fail(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    with pytest.raises(OnnxSessionError, match="CHECKSUM_MISMATCH"):
        OnnxInferenceSession(path, "0" * 64, manifest())
    wrong = manifest()
    wrong = wrong.model_copy(
        update={"inputs": [TensorSpec(name="wrong", shape=[1, 3, 8, 8], dtype="float32")]}
    )
    with pytest.raises(OnnxSessionError, match="INVALID_TENSOR_CONTRACT"):
        OnnxInferenceSession(path, checksum, wrong)
    wrong_output = manifest().model_copy(
        update={"outputs": [TensorSpec(name="wrong", shape=[1, 6, 2], dtype="float32")]}
    )
    with pytest.raises(OnnxSessionError, match="INVALID_TENSOR_CONTRACT"):
        OnnxInferenceSession(path, checksum, wrong_output)
    wrong_dtype = manifest().model_copy(
        update={"inputs": [TensorSpec(name="images", shape=[1, 3, 8, 8], dtype="uint8")]}
    )
    with pytest.raises(OnnxSessionError, match="INVALID_TENSOR_CONTRACT"):
        OnnxInferenceSession(path, checksum, wrong_dtype)


def test_missing_and_invalid_onnx_fail_explicitly(tmp_path: Path) -> None:
    with pytest.raises(OnnxSessionError, match="ARTIFACT_MISSING"):
        OnnxInferenceSession(tmp_path / "absent.onnx", "0" * 64, manifest())
    path = tmp_path / "invalid.onnx"
    payload = b"not an ONNX graph"
    path.write_bytes(payload)
    with pytest.raises(OnnxSessionError, match="MODEL_LOAD_FAILED"):
        OnnxInferenceSession(path, sha256(payload).hexdigest(), manifest())


def test_inference_runtime_error_is_not_empty_detection(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    session = OnnxInferenceSession(path, checksum, manifest())
    with pytest.raises(OnnxSessionError, match="INFERENCE_FAILED"):
        session.run({"images": np.zeros((1, 3, 7, 8), dtype=np.float32)})
    assert session.last_inference_ms is None


def test_unsupported_profile_and_bad_frame_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    session = OnnxInferenceSession(path, checksum, manifest())
    with pytest.raises(YoloContractError, match="UNSUPPORTED_OUTPUT_SEMANTICS"):
        YoloOnnxDetector(session, manifest(output_semantics="detections"))
    detector = YoloOnnxDetector(session, manifest())
    bad = VideoFrame("camera", "session", 1, width=8, height=4, pixel_format="RGB", payload=b"")
    with pytest.raises(YoloContractError, match="INVALID_FRAME_SIZE"):
        detector.detect([bad])
    assert detector.inference_failures == 1


def test_person_filter_and_malformed_output_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    detector = YoloOnnxDetector(OnnxInferenceSession(path, checksum, manifest()), manifest())
    # The first candidate is a car; the second is a person below threshold.
    output = np.array(
        [[[4, 4], [4, 4], [4, 4], [4, 4], [0.1, 0.2], [0.9, 0.1]]],
        dtype=np.float32,
    )
    assert detector.postprocess(output, frame(), 1.0, 0, 2) == []
    output[0, 4, 1] = np.nan
    with pytest.raises(YoloContractError, match="INVALID_MODEL_OUTPUT"):
        detector.postprocess(output, frame(), 1.0, 0, 2)
    detector.close()
    with pytest.raises(OnnxSessionError, match="MODEL_CLOSED"):
        detector.detect([frame()])


def test_empty_valid_output_and_class_count_validation(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    session = OnnxInferenceSession(path, checksum, manifest())
    detector = YoloOnnxDetector(session, manifest())
    output = np.zeros((1, 6, 0), dtype=np.float32)
    assert detector.postprocess(output, frame(), 1.0, 0, 2) == []
    with pytest.raises(YoloContractError, match="INVALID_MODEL_OUTPUT"):
        detector.postprocess(np.zeros((1, 7, 2), dtype=np.float32), frame(), 1.0, 0, 2)
    with pytest.raises(YoloContractError, match="INVALID_CLASS_COUNT"):
        YoloOnnxDetector(session, manifest(class_count=3))
    detector.close()


def test_wrong_model_role_rejected_before_inference(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    session = OnnxInferenceSession(path, checksum, manifest())
    wrong_role = manifest().model_copy(update={"model_type": ModelType.FACE_DETECTOR})
    with pytest.raises(YoloContractError, match="MODEL_TYPE_MISMATCH"):
        YoloOnnxDetector(session, wrong_role)
    session.close()


class Provider:
    def __init__(self, deployment: PendingModelDeployment) -> None:
        self.deployment = deployment
        self.results: list[dict[str, object]] = []

    def pending(self) -> list[PendingModelDeployment]:
        return [self.deployment]

    def report(self, deployment_id: str, **result: object) -> None:
        self.results.append({"deployment_id": deployment_id, **result})


def test_delivery_constructs_real_cpu_detector_without_mock(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    provider = Provider(
        PendingModelDeployment(
            "deployment-1",
            ModelType.PERSON_DETECTOR,
            DeploymentBackend.AUTO,
            path,
            checksum,
            "v1",
            "FP32",
            manifest(),
            "revision-1",
        )
    )
    synchronizer = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    assert synchronizer.sync_once() == ["deployment-1"]
    detector = synchronizer.active_models.active(ModelType.PERSON_DETECTOR)
    assert isinstance(detector, PreparedYoloDetector)
    assert detector.adapter_kind == "yolo_onnx"
    assert synchronizer.active_person_detector() is None  # activation is not assignment
    assert len(detector.detect([frame()])[0]) == 1
    runtime = detector.runtime_status()
    assert runtime.deployment_id == "deployment-1"
    assert runtime.model_version == "v1"
    assert runtime.checksum_prefix == checksum[:12]
    assert runtime.execution_provider == "CPUExecutionProvider"
    assert runtime.inference_count == 1
    assert runtime.inference_failures == 0
    assert runtime.latest_latency_ms is not None
    assert runtime.health == "ACTIVE"
    assert provider.results[0]["success"] is True
    assert provider.results[0]["resolved"].backend is DeploymentBackend.ONNX_CPU


def test_worker_uses_only_assigned_deployment(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    deployment = PendingModelDeployment(
        "selected",
        ModelType.PERSON_DETECTOR,
        DeploymentBackend.ONNX_CPU,
        path,
        checksum,
        "v1",
        "FP32",
        manifest(),
        "revision-1",
    )

    class AssignedProvider(HttpDeploymentProvider):
        def pending(self) -> list[PendingModelDeployment]:
            return []

        def assigned(self) -> list[PendingModelDeployment]:
            return [deployment]

        def report(self, deployment_id: str, **result: object) -> None:
            assert deployment_id == "selected"
            assert result["success"] is True

    synchronizer = ModelDeploymentSynchronizer(
        AssignedProvider("http://unused", "token"),
        RuntimeCapabilities.cpu_only(),
        ProductionAdapterFactory(),
    )
    assert synchronizer.sync_once() == ["selected"]
    detector = synchronizer.active_person_detector()
    assert detector is not None and len(detector.detect([frame()])[0]) == 1
    assert synchronizer.sync_once() == []  # same revision does not reload the session


def test_worker_processes_real_onnx_detection_without_fake_tracks(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    detector = YoloOnnxDetector(OnnxInferenceSession(path, checksum, manifest()), manifest())
    prepared = PreparedYoloDetector("deployment-1", "v1", checksum, detector)
    prepared.warmup()
    config = FaceRecognitionConfig.model_validate_json(
        Path("configuration/examples/face-recognition.example.json").read_text()
    )
    worker = VisionWorker.with_runtime_config(
        config, "worker", "revision-1", 5.0, 4,
        person_detector=prepared, person_detector_status=prepared.runtime_status,
    )
    result = worker.process_frame(frame())
    assert result is not None and len(result.person_detections) == 1
    assert result.track_results == []
    status = worker.status(CameraConnectionState.CONNECTED)
    assert status.worker_health == "degraded"
    assert status.person_detector_runtime is not None
    assert status.person_detector_runtime.inference_count == 1
    assert status.person_detector_runtime.execution_provider == "CPUExecutionProvider"


def test_controlled_onnx_to_real_bot_sort_to_track_state(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    detector = YoloOnnxDetector(OnnxInferenceSession(path, checksum, manifest()), manifest())
    tracker = BoTSortTracker("camera", 5, 1000)
    store = InMemoryTrackStore(ttl_ms=1000)
    pipeline = RuntimePipeline(
        detector, tracker, store, BoundedFrameBuffer(3), FrameSampler(5), "worker", "rev"
    )
    first = pipeline.process_frame(replace(frame(), timestamp_ms=0))
    second = pipeline.process_frame(replace(frame(), sequence=2, timestamp_ms=200))
    assert first is not None and second is not None
    assert len(first.person_detections) == len(first.track_results) == 1
    assert first.track_results[0].track_id == second.track_results[0].track_id
    assert store.active_count == 1
    assert pipeline.status(CameraConnectionState.CONNECTED).worker_health == "healthy"
    pipeline.close()
    detector.close()


def test_explicit_gpu_cannot_activate_cpu_only_adapter(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    provider = Provider(
        PendingModelDeployment(
            "deployment-1",
            ModelType.PERSON_DETECTOR,
            DeploymentBackend.ONNX_CUDA,
            path,
            checksum,
            "v1",
            "FP32",
            manifest(),
            "revision-1",
        )
    )
    synchronizer = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    synchronizer.sync_once()
    assert provider.results[0]["success"] is False
    assert provider.results[0]["error_category"] == "ADAPTER_UNAVAILABLE"


def test_missing_delivery_artifact_and_unsupported_role_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "missing.onnx"
    missing = PendingModelDeployment(
        "missing", ModelType.PERSON_DETECTOR, DeploymentBackend.ONNX_CPU,
        path, "0" * 64, "v1", "FP32", manifest(), "revision-1",
    )
    provider = Provider(missing)
    ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    ).sync_once()
    assert provider.results[0]["error_category"] == "ARTIFACT_MISSING"

    checksum = controlled_graph(path)
    unsupported = PendingModelDeployment(
        "unsupported", ModelType.FACE_DETECTOR, DeploymentBackend.ONNX_CPU,
        path, checksum, "v1", "FP32", manifest(), "revision-1",
    )
    provider = Provider(unsupported)
    ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    ).sync_once()
    assert provider.results[0]["error_category"] == "MODEL_TYPE_MISMATCH"


def test_auto_uses_cpu_when_accelerated_adapters_are_not_implemented(tmp_path: Path) -> None:
    path = tmp_path / "controlled.onnx"
    checksum = controlled_graph(path)
    provider = Provider(
        PendingModelDeployment(
            "deployment-1",
            ModelType.PERSON_DETECTOR,
            DeploymentBackend.AUTO,
            path,
            checksum,
            "v1",
            "FP32",
            manifest(),
            "revision-1",
        )
    )
    capabilities = RuntimeCapabilities(True, True, True)
    synchronizer = ModelDeploymentSynchronizer(provider, capabilities, ProductionAdapterFactory())
    synchronizer.sync_once()
    resolved = provider.results[0]["resolved"]
    assert resolved.backend is DeploymentBackend.ONNX_CPU
    assert resolved.fallback_reason == "CUDA_ADAPTER_UNAVAILABLE"
