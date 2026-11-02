export interface PersonPublic {
  id: string;
  person_number: number;
  full_name: string;
  student_id: string | null;
  status: "ACTIVE" | "DISABLED";
  image_count: number;
  recognition_ready: false;
  created_at: string;
  updated_at: string;
}

export type ImageSource = "MANUAL_UPLOAD" | "CAMERA_CAPTURE";

export interface FaceImagePublic {
  id: string;
  person_id: string;
  original_filename: string;
  mime_type: "image/jpeg";
  width: number;
  height: number;
  processing_state: "PENDING_PROCESSING";
  source: ImageSource;
  created_at: string;
  content_url: string;
  review_state?: "PENDING_REVIEW" | "APPROVED" | "REJECTED";
}

export interface UploadResult {
  filename: string;
  status: "STORED_PENDING" | "REJECTED" | "FAILED";
  image?: FaceImagePublic;
  error_category?: string;
}

export interface PersonPage {
  items: PersonPublic[];
  total: number;
  persistence: "postgresql" | "in_memory";
}

const messages: Record<string, string> = {
  STUDENT_ID_ALREADY_EXISTS: "Mã sinh viên đã tồn tại",
  INVALID_IMAGE: "Ảnh không hợp lệ",
  UNSUPPORTED_IMAGE_FORMAT: "Chỉ hỗ trợ ảnh JPG hoặc PNG",
  IMAGE_TOO_LARGE: "Ảnh vượt quá 10 MB",
  IMAGE_DIMENSIONS_EXCEEDED: "Ảnh vượt quá 20 triệu điểm ảnh",
  MULTIFRAME_IMAGE_NOT_SUPPORTED: "Không hỗ trợ ảnh động hoặc ảnh nhiều khung hình",
  TOO_MANY_FILES: "Mỗi lần chỉ tải tối đa 10 ảnh",
  UPLOAD_REQUEST_TOO_LARGE: "Tổng dung lượng tải lên vượt quá giới hạn",
  INVALID_MULTIPART: "Dữ liệu tải ảnh không hợp lệ",
  PERSON_NOT_FOUND: "Không tìm thấy hồ sơ",
  IMAGE_SAVE_FAILED: "Không lưu được ảnh. Vui lòng thử lại"
};

export function registryError(category?: string): string {
  return messages[category ?? ""] ?? "Không xử lý được yêu cầu. Vui lòng thử lại";
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await apiFetch(url, init); }
  catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error("Mất kết nối với máy chủ. Vui lòng thử lại");
  }
  if (!response.ok) {
    const value: unknown = await response.json().catch(() => null);
    const category = value && typeof value === "object" && "detail" in value
      && typeof value.detail === "string" ? value.detail : undefined;
    throw new Error(category ? registryError(category) : `Yêu cầu thất bại (${response.status})`);
  }
  return await response.json() as T;
}

export function listPersons(search: string, offset: number, signal?: AbortSignal): Promise<PersonPage> {
  const query = new URLSearchParams({ search, offset: String(offset), limit: "20" });
  return request(`/api/v1/persons?${query}`, { signal });
}

export function createPerson(input: { full_name: string; student_id: string }): Promise<PersonPublic> {
  return request("/api/v1/persons", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input)
  });
}

export function listFaceImages(id: string, signal?: AbortSignal): Promise<FaceImagePublic[]> {
  return request<{ items: FaceImagePublic[] }>(`/api/v1/persons/${encodeURIComponent(id)}/face-images`,
    { signal }).then(value => value.items);
}

export function uploadFaceImages(
  id: string, files: File[], source: ImageSource = "MANUAL_UPLOAD"
): Promise<UploadResult[]> {
  const body = new FormData();
  body.set("source", source);
  files.forEach(file => body.append("files", file));
  return request<{ results: UploadResult[] }>(`/api/v1/persons/${encodeURIComponent(id)}/face-images`,
    { method: "POST", body }).then(value => value.results);
}
import { apiFetch } from "./client";
