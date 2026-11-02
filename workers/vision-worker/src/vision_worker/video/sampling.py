from vision_worker.video.frame import VideoFrame


class FrameSampler:
    def __init__(self, target_fps: float) -> None:
        if target_fps <= 0.0:
            raise ValueError("target FPS must be positive")
        self._interval_ms = 1000.0 / target_fps
        self._last_processed_ms: dict[str, int] = {}

    def should_process(self, frame: VideoFrame) -> bool:
        previous = self._last_processed_ms.get(frame.camera_id)
        if previous is None or frame.timestamp_ms < previous:
            self._last_processed_ms[frame.camera_id] = frame.timestamp_ms
            return True
        if frame.timestamp_ms - previous < self._interval_ms:
            return False
        self._last_processed_ms[frame.camera_id] = frame.timestamp_ms
        return True

    def clear(self) -> None:
        self._last_processed_ms.clear()
