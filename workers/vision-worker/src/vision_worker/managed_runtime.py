from threading import Lock, Thread

from fr_contracts.runtime_status import CameraConnectionState

from vision_worker.app import VisionWorker
from vision_worker.face_recognition.embedding_pipeline import FacePipelineError
from vision_worker.inference.onnx_session import OnnxSessionError
from vision_worker.inference.yolo_onnx import YoloContractError
from vision_worker.runtime.status_publisher import RuntimeStatusPublisher
from vision_worker.tracking.bot_sort import TrackerRuntimeError
from vision_worker.video.camera import GStreamerCameraSource


class ThreadedCameraRuntime:
    """Owns one camera source and worker thread for revision-safe replacement."""

    def __init__(
        self,
        source: GStreamerCameraSource,
        worker: VisionWorker,
        status_publisher: RuntimeStatusPublisher | None = None,
    ) -> None:
        self._source = source
        self._worker = worker
        self._status_publisher = status_publisher
        self._lock = Lock()
        self._closed = False
        self._error_category: str | None = None
        self._thread = Thread(target=self._run, daemon=True, name="camera-runtime")
        self._thread.start()

    @property
    def error_category(self) -> str | None:
        with self._lock:
            return self._error_category

    def _run(self) -> None:
        try:
            self._worker.run_camera(
                self._source,
                status_publisher=self._status_publisher,
            )
        except Exception as error:
            # The public error is deliberately categorical; URI-bearing backend errors
            # must never cross the runtime/status boundary.
            category = (
                "MODEL_INFERENCE_ERROR"
                if isinstance(error, (OnnxSessionError, YoloContractError, FacePipelineError))
                else "TRACKER_ERROR"
                if isinstance(error, TrackerRuntimeError)
                else "CAMERA_RUNTIME_FAILED"
            )
            with self._lock:
                self._error_category = category
            if self._status_publisher is not None:
                self._status_publisher.publish(
                    self._worker.status(CameraConnectionState.ERROR).model_copy(
                        update={"error_category": category}
                    )
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._source.close()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise RuntimeError("CAMERA_RUNTIME_SHUTDOWN_TIMEOUT")
        self._worker.close()


class RevisionTrackStateCleaner:
    """Track state is owned by each revision-scoped VisionWorker instance."""

    def clear_camera(self, camera_id: str) -> None:
        # Replacing ThreadedCameraRuntime discards that revision's private track store.
        return None
