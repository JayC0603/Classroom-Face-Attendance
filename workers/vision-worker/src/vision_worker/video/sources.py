from vision_worker.camera_config import ActiveCamera
from vision_worker.video.browser import HttpBrowserCameraBackend
from vision_worker.video.camera import GStreamerCameraSource
from vision_worker.video.gstreamer_inprocess import GStreamerRtspBackend, load_gstreamer
from vision_worker.video.local_webcam import LocalWebcamBackend
from vision_worker.video.preview import PreviewCameraSource, WorkerPreviewPublisher


def create_camera_source(
    config: ActiveCamera,
    api_url: str,
    worker_token: str,
    width: int,
    height: int,
) -> GStreamerCameraSource:
    if config.source_type == "local_webcam":
        if config.device_index is None:
            raise RuntimeError("LOCAL_WEBCAM_DEVICE_REQUIRED")
        source = PreviewCameraSource(
            config.id,
            "",
            "AUTO",
            "local",
            LocalWebcamBackend(config.device_index, width, height),
        )
        source.preview = WorkerPreviewPublisher(
            api_url, config.id, config.revision, worker_token, display_fps=config.display_fps
        )
        return source
    if config.source_type == "webcam":
        if not config.stream_session_id:
            raise RuntimeError("BROWSER_SESSION_REQUIRED")
        source = PreviewCameraSource(
            config.id,
            "",
            "AUTO",
            "browser",
            HttpBrowserCameraBackend(api_url, config.id, config.stream_session_id, worker_token),
        )
        source.raw_preview = False
        source.preview = WorkerPreviewPublisher(
            api_url, config.id, config.revision, worker_token, display_fps=config.display_fps
        )
        return source
    if config.source_type != "rtsp" or not config.rtsp_url:
        raise RuntimeError("INVALID_CAMERA_SOURCE")
    source = PreviewCameraSource(
        config.id,
        config.rtsp_url,
        config.codec,
        "tcp",
        GStreamerRtspBackend(load_gstreamer(), width, height),
    )
    source.preview = WorkerPreviewPublisher(
        api_url, config.id, config.revision, worker_token, display_fps=config.display_fps
    )
    return source
