"""Opt-in local acceptance: operator-supplied media and assigned production artifacts.

No weights are downloaded, no crops/vectors are saved, and no control-plane writes
are made. This test is BLOCKED without all documented environment settings.
"""

import json
import os
from pathlib import Path

import pytest
from fr_config.models import FaceRecognitionConfig
from fr_domain.model_management import ModelType
from vision_worker.face_recognition.embedding_pipeline import (
    FaceModelProvenance,
    FaceObservationStatus,
    TrackFaceProcessor,
)
from vision_worker.model_delivery import (
    HttpDeploymentProvider,
    ModelDeploymentSynchronizer,
    ProductionAdapterFactory,
)
from vision_worker.model_runtime import RuntimeCapabilities
from vision_worker.runtime.pipeline import RuntimePipeline
from vision_worker.runtime.track_store import InMemoryTrackStore
from vision_worker.tracking.bot_sort import BoTSortTracker
from vision_worker.video.buffer import BoundedFrameBuffer
from vision_worker.video.frame import VideoFrame
from vision_worker.video.sampling import FrameSampler

_REQUIRED = (
    "FR_M3_CONTROL_API_URL",
    "FR_WORKER_INTERNAL_TOKEN",
    "FR_M3_FEATURE_CONFIG",
    "FR_M3_LOCAL_MEDIA",
)


@pytest.mark.skipif(
    not all(os.getenv(name) for name in _REQUIRED),
    reason="BLOCKED: supplied deployments, feature config and authorized local media required",
)
def test_real_assigned_models_track_to_face_embedding(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    class AssignedOnlyProvider(HttpDeploymentProvider):
        def pending(self):
            return []  # acceptance never activates unassigned artifacts

        def report(self, deployment_id, **result):
            assert result["success"], "Assigned artifact failed CPU validation/warmup"

    provider = AssignedOnlyProvider(
        os.environ["FR_M3_CONTROL_API_URL"], os.environ["FR_WORKER_INTERNAL_TOKEN"]
    )
    supplied = provider.assigned()
    assert {item.model_type for item in supplied} == set(ModelType)
    feature = FaceRecognitionConfig.model_validate_json(
        Path(os.environ["FR_M3_FEATURE_CONFIG"]).read_bytes()
    )
    media = Path(os.environ["FR_M3_LOCAL_MEDIA"])
    assert media.is_file(), "Authorized local media is unavailable"
    sync = ModelDeploymentSynchronizer(
        provider, RuntimeCapabilities.cpu_only(), ProductionAdapterFactory()
    )
    pipeline = None
    capture = None
    try:
        sync.sync_once()
        detector = sync.active_person_detector()
        face_detector = sync.active_face_detector()
        embedder = sync.active_face_embedder()
        assert detector is not None and face_detector is not None and embedder is not None
        face_processor = TrackFaceProcessor(
            face_detector,
            embedder,
            FaceModelProvenance(
                face_detector.deployment_id,
                face_detector.execution_provider,
                embedder.deployment_id,
                embedder.execution_provider,
                embedder.model_definition_id,
                embedder.model_version_id,
                embedder.model_version,
                embedder.embedding_dimension,
            ),
            feature.quality,
            feature.timing,
        )
        pipeline = RuntimePipeline(
            detector,
            BoTSortTracker(feature.camera_id, 5, feature.timing.track_cache_ttl_ms),
            InMemoryTrackStore(feature.timing.track_cache_ttl_ms),
            BoundedFrameBuffer(2),
            FrameSampler(5),
            "real-local-acceptance",
            "operator-supplied",
            face_processor=face_processor,
        )
        still = cv2.imread(str(media))
        if still is None:
            capture = cv2.VideoCapture(str(media))
            assert capture.isOpened(), "Local video could not open"
        observation = None
        for index in range(300 if capture is not None else 1):
            if capture is not None:
                ok, pixels = capture.read()
                if not ok:
                    break
                timestamp_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC))
            else:
                pixels, timestamp_ms = still, 0
            rgb = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
            frame = VideoFrame(
                feature.camera_id,
                "real-local-acceptance",
                index,
                timestamp_ms,
                rgb.shape[1],
                rgb.shape[0],
                "RGB",
                rgb.tobytes(),
            )
            result = pipeline.process_frame(frame)
            if result is None:
                continue
            observation = next(
                (
                    item
                    for item in result.face_observations
                    if item.status is FaceObservationStatus.EMBEDDED
                ),
                None,
            )
            if observation is not None:
                break
        assert observation is not None, "No quality-accepted track embedding in supplied media"
        assert observation.face is not None and observation.face.landmarks is not None
        assert observation.alignment is not None and observation.alignment.width == 112
        assert observation.embedding is not None
        values = np.asarray(observation.embedding.values)
        assert values.shape == (embedder.embedding_dimension,) and np.isfinite(values).all()
        norm = float(np.linalg.norm(values))
        assert norm == pytest.approx(1.0, abs=1e-3)
        # Only safe evidence is saved in pytest's temporary location, never raw biometrics.
        evidence = {
            "status": "TESTED_WITH_REAL_MODEL",
            "source_type": "local_video" if capture is not None else "local_frame",
            "models": [
                {
                    "role": item.model_type.value,
                    "filename": item.artifact_path.name,
                    "sha256": item.artifact_checksum,
                    "provider": "CPUExecutionProvider",
                    "inputs": [spec.model_dump() for spec in item.manifest.inputs],
                    "outputs": [spec.model_dump() for spec in item.manifest.outputs],
                }
                for item in supplied
            ],
            "track_id": observation.track_id,
            "embedding_dimension": len(values),
            "embedding_norm": norm,
            "metrics": face_processor.status().model_dump(),
            "no_mock_adapters": True,
        }
        (tmp_path / "real-face-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    finally:
        if capture is not None:
            capture.release()
        if pipeline is not None:
            pipeline.close()
        sync.active_models.close_all()
