from collections import deque
from dataclasses import dataclass

from vision_worker.video.frame import VideoFrame


@dataclass(frozen=True, slots=True)
class BufferUsage:
    frames: int
    capacity: int


class BoundedFrameBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("ring-buffer capacity must be positive")
        self._capacity = capacity
        self._frames: deque[VideoFrame] = deque(maxlen=capacity)
        self._overwritten_frames = 0

    def append(self, frame: VideoFrame) -> None:
        if len(self._frames) == self._capacity:
            self._overwritten_frames += 1
        self._frames.append(frame)

    def snapshot(self) -> tuple[VideoFrame, ...]:
        return tuple(self._frames)

    def clear(self) -> None:
        self._frames.clear()

    @property
    def usage(self) -> BufferUsage:
        return BufferUsage(frames=len(self._frames), capacity=self._capacity)

    @property
    def overwritten_frames(self) -> int:
        """Frames evicted from rolling history, not frames lost by ingestion."""
        return self._overwritten_frames
