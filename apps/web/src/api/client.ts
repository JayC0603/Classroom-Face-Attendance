import type {
  CameraConnectionResult,
  CameraInput,
  CameraPublic,
  MessageEnvelope,
  RuntimeStatus,
  ModelDefinitionPublic,
  ModelDeploymentPublic,
  ModelType,
  DeploymentBackend,
  RecognitionReadiness
} from "./contracts";

export interface HealthStatus {
  service: string;
  status: string;
  external_dependencies: string;
  camera_persistence?: string;
}

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(value: string | null): void {
  csrfToken = value;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body !== undefined && !isForm && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(url, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401) unauthorizedHandler?.();
  return response;
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, init);
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = body && typeof body === "object" && "detail" in body
      && typeof body.detail === "string" ? body.detail : undefined;
    throw new Error(detail ?? `Request failed: ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return await response.json() as T;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, init);
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const category = body && typeof body === "object" && "error_category" in body
      ? body.error_category : undefined;
    const detail = body && typeof body === "object" && "detail" in body
      ? body.detail : undefined;
    if (url.startsWith("/api/v1/cameras") && response.status === 422
      && (category === "VALIDATION_ERROR" || detail === "INVALID_CAMERA_SOURCE")) {
      throw new Error("Máy chủ từ chối cấu hình camera. Hãy kiểm tra nguồn camera và tải lại trang.");
    }
    if (response.status >= 500) {
      throw new Error(`Máy chủ gặp lỗi (${response.status}). Vui lòng thử lại.`);
    }
    if (response.status === 409 && detail === 'CAMERA_REVISION_CONFLICT') {
      throw new Error('Camera đã được thay đổi ở phiên khác. Hủy chỉnh sửa và tải lại danh sách trước khi thử lại.');
    }
    if (response.status === 409 && detail === 'CAMERA_IN_USE_BY_CLASS_SESSION') {
      throw new Error('Camera đang được liên kết với buổi học. Hãy giữ camera và tắt nguồn hình, hoặc cập nhật liên kết buổi học trước khi xóa.');
    }
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthStatus> {
  return json("/health");
}

export function listCameras(): Promise<CameraPublic[]> {
  return json<{ items: CameraPublic[] }>("/api/v1/cameras").then((value) => value.items);
}

export function createCamera(input: CameraInput): Promise<CameraPublic> {
  return json("/api/v1/cameras", { method: "POST", body: JSON.stringify(input) });
}

export function updateCamera(
  id: string,
  input: Partial<CameraInput> & {expected_revision?: string}
): Promise<CameraPublic> {
  return json(`/api/v1/cameras/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export function testCamera(id: string): Promise<CameraConnectionResult> {
  return json(`/api/v1/cameras/${id}/test-connection`, { method: "POST" });
}

export async function deleteCamera(id: string, revision: string): Promise<void> {
  const response = await apiFetch(`/api/v1/cameras/${encodeURIComponent(id)}?expected_revision=${encodeURIComponent(revision)}`, {method: 'DELETE'});
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  if (body.detail === 'CAMERA_IN_USE_BY_CLASS_SESSION') throw new Error('Camera đã liên kết với buổi học nên chưa thể xóa. Bạn có thể tắt camera để ngừng nhận hình.');
  if (body.detail === 'CAMERA_REVISION_CONFLICT') throw new Error('Camera vừa thay đổi. Tải lại danh sách trước khi xóa.');
  throw new Error('Không xóa được camera. Tải lại danh sách rồi thử lại.');
}

export function activateCamera(id: string): Promise<CameraPublic> {
  return json(`/api/v1/cameras/${id}/activate`, { method: "POST" });
}

export function connectRuntimeStatus(
  onStatus: (status: RuntimeStatus) => void
): () => void {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  let stopped = false;
  let socket: WebSocket | undefined;
  let retry: ReturnType<typeof setTimeout>;
  let attempts = 0;
  let snapshot: AbortController | undefined;
  function valid(status: RuntimeStatus): boolean {
    return typeof status?.camera_id === 'string' && typeof status.worker_id === 'string'
      && ['input_fps', 'processed_fps', 'active_tracks', 'dropped_frames', 'ring_buffer_frames', 'ring_buffer_capacity'].every(key => Number.isFinite(status[key as keyof RuntimeStatus]));
  }
  function connect() {
    if (stopped) return;
    socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`);
    socket.addEventListener('open', () => {
      attempts = 0;
      snapshot?.abort(); snapshot = new AbortController();
      void requestJson<{items: RuntimeStatus[]}>('/api/v1/runtime/status', {signal: snapshot.signal}).then(result => {
        if (!stopped) result.items.filter(valid).slice(-256).forEach(onStatus);
      }).catch(() => { /* Subsequent stream updates remain available. */ });
    });
    socket.addEventListener('message', event => {
      if (stopped) return;
      try {
        const message = JSON.parse(String(event.data)) as MessageEnvelope<RuntimeStatus>;
        if (message.type === 'runtime.status' && valid(message.payload)) onStatus(message.payload);
      } catch { /* Ignore malformed messages; do not tear down the observation plane. */ }
    });
    socket.addEventListener('close', event => {
      if (stopped) return;
      if (event.code === 1008) {stopped = true; unauthorizedHandler?.(); return;}
      retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) + Math.random() * 500);
    });
  }
  connect();
  return () => {stopped = true; clearTimeout(retry); snapshot?.abort(); socket?.close();};
}

export function listModels(): Promise<ModelDefinitionPublic[]> {
  return json<{ items: ModelDefinitionPublic[] }>("/api/v1/models").then((value) => value.items);
}

export function createModel(input: {
  name: string;
  model_type: ModelType;
  framework: string;
  description?: string;
}): Promise<ModelDefinitionPublic> {
  return json("/api/v1/models", { method: "POST", body: JSON.stringify(input) });
}

export async function uploadModelVersion(
  definitionId: string,
  version: string,
  manifest: object,
  file: File
): Promise<void> {
  const body = new FormData();
  body.set("version", version);
  body.set("manifest", JSON.stringify(manifest));
  body.set("file", file);
  const response = await apiFetch(`/api/v1/models/${definitionId}/versions`, {
    method: "POST",
    body
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(detail?.detail ?? `Upload failed: ${response.status}`);
  }
}

export function createModelDeployment(
  versionId: string,
  backend: DeploymentBackend
): Promise<ModelDeploymentPublic> {
  return json(`/api/v1/model-versions/${versionId}/deployments`, {
    method: "POST",
    body: JSON.stringify({ backend, precision: "FP32" })
  });
}

export function activateModelDeployment(id: string): Promise<ModelDeploymentPublic> {
  return json(`/api/v1/deployments/${id}/activate`, { method: "POST" });
}

export function assignModel(modelType: ModelType, deploymentId: string): Promise<void> {
  return json("/api/v1/model-assignments", {
    method: "PUT",
    body: JSON.stringify({ model_type: modelType, deployment_id: deploymentId })
  }).then(() => undefined);
}

export function getRecognitionReadiness(): Promise<RecognitionReadiness> {
  return json("/api/v1/recognition/readiness");
}
