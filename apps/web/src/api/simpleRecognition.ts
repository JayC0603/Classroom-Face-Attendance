export interface FacePresenceEvent {
  id: string;
  kind: 'FACE_CAPTURED' | 'UNKNOWN_ALERT';
  camera_id: string;
  camera_name: string;
  track_id: number | null;
  recognized_at: string;
  consecutive_unknown: number;
  evidence_status: 'PENDING' | 'READY' | 'FAILED' | 'EXPIRED';
  evidence_key: string | null;
  evidence_error?: string | null;
}

export interface FaceObservation {
  box?: number[];
  state: 'PENDING' | 'VERIFIED' | 'UNKNOWN' | 'UNCERTAIN';
  full_name: string | null;
  student_id: string | null;
  person_number: number | null;
  similarity: number;
}

export interface CameraRecognitionResult {
  state: string;
  faces: FaceObservation[];
  alerts?: FacePresenceEvent[];
  captures?: FacePresenceEvent[];
  unknown_alert_frames?: number;
  persons?: Array<{
    box: number[];
    confidence: number;
    track_id?: number | null;
    identity_state?: 'PENDING' | 'VERIFIED' | 'UNKNOWN';
    person_number?: number | null;
    full_name?: string | null;
    student_id?: string | null;
  }>;
  person_state?: string;
  person_deployment_id?: string;
  frame_id?: string;
  preview?: string;
  preview_remaining_ms?: number;
  stream_session_id?: string;
}

export interface SimpleRecognitionStatus {
  state: string;
  model_names: string[];
  reference_people?: number;
  reference_images?: number;
  images?: Array<{ person_id: string; image_id: string; state: string }>;
}

export async function getSimpleRecognitionStatus(signal?: AbortSignal, refresh = false): Promise<SimpleRecognitionStatus> {
  const response = await apiFetch(`/api/v1/recognition/simple/${refresh ? 'refresh' : 'status'}`, {
    method: refresh ? 'POST' : 'GET', signal, cache: 'no-store'
  });
  if (!response.ok) throw new Error('Không tải được trạng thái model.');
  return response.json();
}

export async function recognizeCamera(cameraId: string, revision: string, signal: AbortSignal, includePreview = false): Promise<CameraRecognitionResult> {
  const preview = includePreview ? '&include_preview=true' : '';
  const response = await apiFetch(`/api/v1/cameras/${encodeURIComponent(cameraId)}/recognition?revision=${encodeURIComponent(revision)}${preview}`, {
    signal, cache: 'no-store'
  });
  if (!response.ok) throw new Error('Không nhận được kết quả so sánh.');
  return response.json();
}
import { apiFetch } from "./client";
