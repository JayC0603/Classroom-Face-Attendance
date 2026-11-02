export type RecognitionState = "PENDING" | "VERIFIED" | "UNCERTAIN" | "UNKNOWN";

export interface MessageEnvelope<TPayload> {
  schema_version: 1;
  message_id: string;
  type: string;
  timestamp: string;
  camera_id: string | null;
  payload: TPayload;
}

export interface RuntimeStatus {
  worker_id: string;
  camera_id: string;
  camera_state:
    | "DISABLED"
    | "DISCONNECTED"
    | "CONNECTING"
    | "CONNECTED"
    | "RECONNECTING"
    | "ERROR"
    | "FAILED"
    | "STOPPED";
  input_fps: number;
  processed_fps: number;
  latest_processing_latency_ms?: number | null;
  tracker_state?: "ACTIVE" | "UNAVAILABLE" | "ERROR";
  person_detector_runtime?: {health: "ACTIVE" | "ERROR"; model_version: string; latest_latency_ms: number | null} | null;
  dropped_frames: number;
  active_tracks: number;
  ring_buffer_frames: number;
  ring_buffer_capacity: number;
  worker_health: "healthy" | "degraded" | "unhealthy";
  active_config_revision: string | null;
  stream_session_id: string | null;
  reconnect_count: number;
  last_connected_at: string | null;
  last_frame_at: string | null;
  error_category: string | null;
}

export type CameraCodec = "AUTO" | "H264" | "H265";
export type CameraSourceType = "rtsp" | "local_webcam" | "webcam";

export interface CameraPublic {
  id: string;
  name: string;
  source_type: CameraSourceType;
  device_index?: number | null;
  rtsp_url: string | null;
  codec: CameraCodec;
  enabled: boolean;
  sampling_fps: number;
  display_fps?: number;
  roi_polygon?: Array<[number, number]>;
  attendance_enabled?: boolean;
  evidence_retention_days?: number;
  unknown_alert_frames?: number;
  lifecycle: "DRAFT" | "ACTIVE";
  revision: string;
}

export interface CameraInput {
  name: string;
  source_type?: CameraSourceType;
  device_index?: number;
  rtsp_url?: string;
  codec: CameraCodec;
  enabled: boolean;
  sampling_fps: number;
  display_fps?: number;
  roi_polygon?: Array<[number, number]>;
  attendance_enabled?: boolean;
  evidence_retention_days?: number;
  unknown_alert_frames?: number;
}

export interface CameraConnectionResult {
  status: string;
  error_category: string | null;
}

export type ModelType = "PERSON_DETECTOR" | "FACE_DETECTOR" | "FACE_EMBEDDER";
export type DeploymentBackend = "AUTO" | "ONNX_CPU" | "ONNX_CUDA" | "TENSORRT";

export interface ModelVersionPublic {
  id: string;
  version: string;
  format: "ONNX" | "TENSORRT_ENGINE";
  checksum: string;
  size_bytes: number;
  validation_status: "UPLOADED" | "VALIDATING" | "VALID" | "INVALID";
  validation_error: string | null;
  manifest: { inputs: Array<{ name: string; shape: Array<number | string>; dtype: string }> };
}

export interface ModelDeploymentPublic {
  id: string;
  model_version_id: string;
  backend: DeploymentBackend;
  resolved_backend: DeploymentBackend | null;
  precision: string;
  status: "DRAFT" | "ACTIVATION_PENDING" | "ACTIVE" | "INACTIVE" | "ERROR";
  error_category: string | null;
}

export interface ModelDefinitionPublic {
  id: string;
  name: string;
  model_type: ModelType;
  framework: string;
  description: string;
  versions: ModelVersionPublic[];
  deployments: ModelDeploymentPublic[];
}

export interface RecognitionReadiness {
  status: "READY" | "NOT_READY";
  reasons: string[];
  assignments: Partial<Record<ModelType, string>>;
  vector_index_status: string;
}
