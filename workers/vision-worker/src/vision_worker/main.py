import argparse
import json
import os
import sys
import time
from hashlib import sha256
from pathlib import Path

import httpx
from fr_config.models import CameraConfig, FaceRecognitionConfig

from vision_worker.app import VisionWorker
from vision_worker.camera_config import (
    ActiveCamera,
    CameraConfigSynchronizer,
    CameraRuntimeManager,
    HttpActiveCameraProvider,
)
from vision_worker.capture_runtime import CaptureOnlyRuntime
from vision_worker.face_recognition.embedding_pipeline import (
    FaceModelProvenance,
    TrackFaceProcessor,
)
from vision_worker.managed_runtime import RevisionTrackStateCleaner, ThreadedCameraRuntime
from vision_worker.model_delivery import (
    HttpDeploymentProvider,
    ModelDeploymentSynchronizer,
    ProductionAdapterFactory,
    discover_runtime_capabilities,
)
from vision_worker.model_runtime import AtomicModelSet
from vision_worker.runtime.pipeline import FrameProcessingResult
from vision_worker.runtime.status_publisher import HttpRuntimeStatusPublisher
from vision_worker.tracking.byte_track import ByteTrackPersonTracker
from vision_worker.video.camera import GStreamerCameraSource
from vision_worker.video.frame import VideoFrame
from vision_worker.video.gstreamer_inprocess import GStreamerRtspBackend, load_gstreamer
from vision_worker.video.preview import PreviewCameraSource
from vision_worker.video.sources import create_camera_source


def _read_feature_config(path: Path) -> tuple[FaceRecognitionConfig, bytes]:
    payload = path.read_bytes()
    return FaceRecognitionConfig.model_validate_json(payload), payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Face Recognition vision worker")
    parser.add_argument("--once", action="store_true", help="Run one smoke/sync cycle")
    parser.add_argument("--camera-config", type=Path)
    parser.add_argument("--feature-config", type=Path)
    parser.add_argument("--control-api-url")
    parser.add_argument("--camera-sync-interval", type=float, default=2.0)
    parser.add_argument("--status-url")
    parser.add_argument("--frame-limit", type=int)
    parser.add_argument("--worker-id", default="worker-local")
    parser.add_argument("--processing-fps", type=float)
    parser.add_argument("--ring-buffer-capacity", type=int, default=100)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    if args.camera_sync_interval <= 0:
        parser.error("--camera-sync-interval must be positive")
    if args.control_api_url:
        if args.camera_config is not None:
            parser.error("--camera-config cannot be combined with --control-api-url")
        worker_token = os.getenv("FR_WORKER_INTERNAL_TOKEN")
        if args.feature_config is None or not worker_token:
            parser.error(
                "--feature-config and FR_WORKER_INTERNAL_TOKEN are required with --control-api-url"
            )
        feature, _ = _read_feature_config(args.feature_config)
        provider = HttpActiveCameraProvider(args.control_api_url, worker_token)
        model_synchronizer = ModelDeploymentSynchronizer(
            HttpDeploymentProvider(args.control_api_url, worker_token),
            discover_runtime_capabilities(),
            ProductionAdapterFactory(),
            active_models=AtomicModelSet(defer_retirement=True),
        )
        status_url = args.status_url or (
            f"{args.control_api_url.rstrip('/')}/api/v1/runtime/status"
        )

        def factory(config: ActiveCamera) -> ThreadedCameraRuntime | CaptureOnlyRuntime:
            detector = model_synchronizer.active_person_detector()
            source = create_camera_source(
                config,
                args.control_api_url,
                worker_token,
                args.width,
                args.height,
            )
            if detector is None:
                return CaptureOnlyRuntime(
                    source,
                    config,
                    args.worker_id,
                    HttpRuntimeStatusPublisher(status_url, worker_token=worker_token),
                )
            camera_feature = feature.model_copy(update={"camera_id": config.id})
            deployment_id = detector.deployment_id

            def publish_processed(frame: VideoFrame, result: FrameProcessingResult) -> None:
                if isinstance(source, PreviewCameraSource):
                    source.preview.set_detections(result.track_results, deployment_id)
                    if not source.raw_preview:
                        source.preview.offer(frame, result.track_results, deployment_id)

            processing_fps = min(args.processing_fps or config.sampling_fps, config.sampling_fps)
            face_detector = model_synchronizer.active_face_detector()
            face_embedder = model_synchronizer.active_face_embedder()
            face_processor = None
            if face_detector is not None and face_embedder is not None:
                face_processor = TrackFaceProcessor(
                    face_detector,
                    face_embedder,
                    FaceModelProvenance(
                        face_detector.deployment_id,
                        face_detector.execution_provider,
                        face_embedder.deployment_id,
                        face_embedder.execution_provider,
                        face_embedder.model_definition_id,
                        face_embedder.model_version_id,
                        face_embedder.model_version,
                        face_embedder.embedding_dimension,
                    ),
                    camera_feature.quality,
                    camera_feature.timing,
                )
            worker = VisionWorker.with_runtime_config(
                camera_feature,
                worker_id=args.worker_id,
                config_revision=config.revision,
                processing_fps=processing_fps,
                ring_buffer_capacity=args.ring_buffer_capacity,
                person_detector=detector,
                tracker=ByteTrackPersonTracker(
                    config.id, processing_fps, camera_feature.timing.track_cache_ttl_ms
                ),
                person_detector_status=detector.runtime_status,
                face_processor=face_processor,
                processed_frame_callback=publish_processed,
                roi_polygon=config.roi_polygon,
            )
            return ThreadedCameraRuntime(
                source,
                worker,
                HttpRuntimeStatusPublisher(status_url, worker_token=worker_token),
            )

        manager = CameraRuntimeManager(factory, RevisionTrackStateCleaner())
        synchronizer = CameraConfigSynchronizer(provider, manager)
        model_revision = 0
        active_deployments: tuple[int | None, int | None, int | None] = (None, None, None)
        connection_failures = 0
        try:
            while True:
                try:
                    model_changes = model_synchronizer.sync_once()
                    detector = model_synchronizer.active_person_detector()
                    face_detector = model_synchronizer.active_face_detector()
                    face_embedder = model_synchronizer.active_face_embedder()
                    current_deployments = (
                        id(detector) if detector is not None else None,
                        id(face_detector) if face_detector is not None else None,
                        id(face_embedder) if face_embedder is not None else None,
                    )
                    if current_deployments != active_deployments:
                        model_revision += 1
                        active_deployments = current_deployments
                    changed = synchronizer.sync_once(runtime_revision=str(model_revision))
                    connection_failures = 0
                except httpx.HTTPError:
                    if args.once:
                        raise
                    connection_failures += 1
                    delay = min(
                        30.0,
                        max(1.0, args.camera_sync_interval) * 2 ** min(connection_failures - 1, 4),
                    )
                    print(
                        json.dumps(
                            {"error_category": "CONTROL_API_UNAVAILABLE", "retry_seconds": delay}
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                # The camera manager has joined old reader threads before retired
                # ONNX sessions can be released.
                model_synchronizer.active_models.close_retired()
                if args.once:
                    print(
                        json.dumps(
                            {
                                "changed_camera_ids": changed,
                                "processed_model_deployment_ids": model_changes,
                            }
                        )
                    )
                    return
                time.sleep(args.camera_sync_interval)
        finally:
            manager.close_all()
            model_synchronizer.active_models.close_all()

    if (args.camera_config is None) != (args.feature_config is None):
        parser.error("--camera-config and --feature-config must be provided together")

    if args.camera_config is not None and args.feature_config is not None:
        camera_bytes = args.camera_config.read_bytes()
        camera = CameraConfig.model_validate_json(camera_bytes)
        feature, feature_bytes = _read_feature_config(args.feature_config)
        if not camera.enabled or not feature.enabled:
            parser.error("camera and feature configuration must both be enabled")
        if camera.id != feature.camera_id:
            parser.error("camera config id must match feature config camera_id")

        revision = sha256(feature_bytes).hexdigest()[:12]
        worker = VisionWorker.with_runtime_config(
            feature,
            worker_id=args.worker_id,
            config_revision=revision,
            processing_fps=args.processing_fps or 5.0,
            ring_buffer_capacity=args.ring_buffer_capacity,
        )
        backend = GStreamerRtspBackend(
            load_gstreamer(),
            width=args.width,
            height=args.height,
        )
        source = GStreamerCameraSource(
            camera_id=camera.id,
            uri=str(camera.stream.uri),
            codec=camera.stream.codec,
            transport=camera.stream.transport,
            backend=backend,
        )
        status_token = os.getenv("FR_WORKER_INTERNAL_TOKEN")
        if args.status_url and not status_token:
            parser.error("FR_WORKER_INTERNAL_TOKEN is required with --status-url")
        publisher = (
            HttpRuntimeStatusPublisher(args.status_url, worker_token=status_token)
            if args.status_url
            else None
        )
        limit = 1 if args.once and args.frame_limit is None else args.frame_limit
        summary = worker.run_camera(source, limit=limit, status_publisher=publisher)
        print(
            json.dumps(
                {
                    "frames_received": summary.frames_received,
                    "frames_processed": summary.frames_processed,
                    "camera_state": summary.camera_state,
                }
            )
        )
        return

    worker = VisionWorker.with_stub_adapters()
    print(json.dumps(worker.run_once()))
    if not args.once:
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
