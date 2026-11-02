export interface PersonModel {
  id: string;
  version: string;
  architecture?: string;
  input_shape: Array<number | string>;
  output_shape: Array<number | string>;
  decode_mode: string;
  person_class_id: number;
  confidence: number;
  checksum_prefix: string;
  deployments: Array<{id: string; status: string; runtime_state: string; error_category: string | null}>;
}

export interface PersonModels {
  items: PersonModel[];
  active_deployment_id: string | null;
  max_upload_bytes: number;
}

export async function getPersonModels(signal?: AbortSignal): Promise<PersonModels> {
  const response = await apiFetch('/api/v1/person-models', {signal, cache: 'no-store'});
  if (!response.ok) throw new Error('Không tải được danh sách model người.');
  return response.json();
}

export async function uploadPersonModel(data: FormData): Promise<void> {
  const response = await apiFetch('/api/v1/person-models', {method: 'POST', body: data});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const category = typeof body.detail === 'string' ? body.detail : 'INVALID_YOLOX_PROFILE';
    if (category === 'YOLOX_OUTPUT_MODE_REQUIRED') throw new Error('Không xác định chắc chắn kiểu đầu ra. Chọn raw hoặc đã decode theo cấu hình export của model.');
    if (category === 'YOLOX_XYXY_SCORE_CLASS_EXPORT_SELECT_MODE') throw new Error('Model xuất xyxy + score + class ID. Chọn Tự nhận dạng hoặc YOLOX xyxy để tải lên.');
    throw new Error(`Không tải được model: ${category}. Kiểm tra file ONNX, phiên bản và cấu hình đầu ra.`);
  }
}

const personModelErrors: Record<string, string> = {
  ARTIFACT_IN_USE: 'Model đang hoạt động hoặc được chọn cho camera. Chọn model khác và ngừng các bản triển khai trước khi xóa.',
  MODEL_VERSION_EXISTS: 'Tên phiên bản đã tồn tại. Hãy chọn tên khác.',
  MODEL_PROFILE_IMMUTABLE_AFTER_DEPLOYMENT: 'Model đã có bản triển khai. Hãy tải phiên bản mới để đổi ngưỡng phát hiện.',
  PERSON_MODEL_NOT_FOUND: 'Model không còn tồn tại. Tải lại danh sách.',
};

async function managePersonModel(id: string, method: string, input?: object): Promise<void> {
  const response = await apiFetch(`/api/v1/person-models/${encodeURIComponent(id)}`, {
    method, ...(input ? {body: JSON.stringify(input)} : {}),
  });
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  throw new Error(personModelErrors[body.detail] ?? 'Không lưu được thay đổi model. Kiểm tra dữ liệu rồi thử lại.');
}

export function updatePersonModel(id: string, version: string, confidence?: number): Promise<void> {
  return managePersonModel(id, 'PATCH', {version, ...(confidence !== undefined ? {confidence} : {})});
}

export function deletePersonModel(id: string): Promise<void> {
  return managePersonModel(id, 'DELETE');
}

export async function deactivatePersonDeployment(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/deployments/${encodeURIComponent(id)}/deactivate`, {method: 'POST'});
  if (!response.ok) throw new Error('Không ngừng được bản triển khai. Tải lại danh sách rồi thử lại.');
}
import { apiFetch } from "./client";
