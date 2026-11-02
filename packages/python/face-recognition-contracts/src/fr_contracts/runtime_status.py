from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CameraConnectionState(StrEnum):
    DISABLED = "DISABLED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class PersonDetectorRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    model_version: str
    checksum_prefix: str
    execution_provider: str
    loaded_at: str
    inference_count: int = Field(ge=0)
    inference_failures: int = Field(ge=0)
    latest_latency_ms: float | None = Field(default=None, ge=0.0)
    mean_latency_ms: float | None = Field(default=None, ge=0.0)
    health: Literal["ACTIVE", "ERROR"]


class StageLatencySummary(BaseModel):
    """Statistics over a bounded recent window; counts span this runtime lifetime."""

    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(default=0, ge=0, le=256)
    attempt_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    mean_ms: float | None = Field(default=None, ge=0.0)
    median_ms: float | None = Field(default=None, ge=0.0)
    p95_ms: float | None = Field(default=None, ge=0.0)


class FacePipelineRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["NOT_CONFIGURED", "ACTIVE", "ERROR"] = "NOT_CONFIGURED"
    face_detector_deployment_id: str | None = None
    face_embedder_deployment_id: str | None = None
    face_detector_provider: str | None = None
    face_embedder_provider: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)
    face_detection_attempts: int = Field(default=0, ge=0)
    face_detection_successes: int = Field(default=0, ge=0)
    no_face_count: int = Field(default=0, ge=0)
    face_detection_failures: int = Field(default=0, ge=0)
    quality_accepted: int = Field(default=0, ge=0)
    quality_rejected: int = Field(default=0, ge=0)
    quality_rejection_reasons: dict[str, int] = Field(default_factory=dict)
    alignment_failures: int = Field(default=0, ge=0)
    embedding_attempts: int = Field(default=0, ge=0)
    embedding_successes: int = Field(default=0, ge=0)
    embedding_failures: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    latest_error_category: str | None = None
    stage_latencies: dict[str, StageLatencySummary] = Field(default_factory=dict)
    latest_roi_ms: float | None = Field(default=None, ge=0.0)
    latest_face_detection_ms: float | None = Field(default=None, ge=0.0)
    latest_quality_ms: float | None = Field(default=None, ge=0.0)
    latest_alignment_ms: float | None = Field(default=None, ge=0.0)
    latest_embedding_ms: float | None = Field(default=None, ge=0.0)
    latest_total_latency_ms: float | None = Field(default=None, ge=0.0)
    latest_scrfd_preprocessing_ms: float | None = Field(default=None, ge=0.0)
    latest_scrfd_inference_ms: float | None = Field(default=None, ge=0.0)
    latest_scrfd_postprocessing_ms: float | None = Field(default=None, ge=0.0)
    latest_adaface_preprocessing_ms: float | None = Field(default=None, ge=0.0)
    latest_adaface_inference_ms: float | None = Field(default=None, ge=0.0)
    latest_adaface_postprocessing_ms: float | None = Field(default=None, ge=0.0)


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    camera_state: CameraConnectionState
    input_fps: float = Field(ge=0.0)
    processed_fps: float = Field(ge=0.0)
    dropped_frames: int = Field(ge=0)
    received_frames: int = Field(default=0, ge=0)
    sampled_frames: int = Field(default=0, ge=0)
    processed_frames: int = Field(default=0, ge=0)
    ring_buffer_overwrites: int = Field(default=0, ge=0)
    active_tracks: int = Field(ge=0)
    lost_tracks: int = Field(default=0, ge=0)
    created_tracks: int = Field(default=0, ge=0)
    expired_tracks: int = Field(default=0, ge=0)
    tracker_state: Literal["ACTIVE", "UNAVAILABLE", "ERROR"] = "UNAVAILABLE"
    tracker_updates: int = Field(default=0, ge=0)
    tracker_failures: int = Field(default=0, ge=0)
    latest_tracker_latency_ms: float | None = Field(default=None, ge=0.0)
    mean_tracker_latency_ms: float | None = Field(default=None, ge=0.0)
    latest_processing_latency_ms: float | None = Field(default=None, ge=0.0)
    ring_buffer_frames: int = Field(ge=0)
    ring_buffer_capacity: int = Field(gt=0)
    worker_health: Literal["healthy", "degraded", "unhealthy"]
    active_config_revision: str | None = None
    stream_session_id: str | None = None
    reconnect_count: int = Field(default=0, ge=0)
    last_connected_at: str | None = None
    last_frame_at: str | None = None
    error_category: str | None = None
    person_detector_runtime: PersonDetectorRuntimeStatus | None = None
    face_pipeline_runtime: FacePipelineRuntimeStatus = Field(
        default_factory=FacePipelineRuntimeStatus
    )
