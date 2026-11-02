export interface BrowserSession {
  session_id: string;
  token: string;
  expires_in_seconds: number;
}

const errors: Record<string, string> = {
  WEBCAM_IN_USE: "Camera đang có phiên truyền khác. Hãy dừng phiên đó hoặc chờ 10 giây rồi thử lại.",
  WEBCAM_CAMERA_NOT_ACTIVE: "Hãy kích hoạt và bật camera nguồn Webcam trước khi chạy.",
  WEBCAM_SESSION_UNAUTHORIZED: "Phiên webcam đã hết hạn. Hãy bắt đầu lại.",
  WEBCAM_FRAME_TOO_LARGE: "Khung hình webcam vượt quá dung lượng cho phép.",
  WEBCAM_SESSION_LIMIT: "Đã đạt số phiên webcam tối đa. Hãy dừng một phiên rồi thử lại.",
  WEBCAM_CAMERA_CHANGED: "Cấu hình camera đã thay đổi. Hãy kích hoạt và bắt đầu lại webcam."
};

async function requireSuccess(response: Response): Promise<void> {
  if (response.ok) return;
  const body: unknown = await response.json().catch(() => null);
  const detail = body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
    ? body.detail : "";
  throw new Error(errors[detail] ?? "Không gửi được hình ảnh tới máy chủ. Hãy thử bắt đầu lại.");
}

export async function startBrowserSession(id: string): Promise<BrowserSession> {
  const response = await apiFetch(`/api/v1/cameras/${encodeURIComponent(id)}/webcam/session`, { method: "POST" });
  await requireSuccess(response);
  return await response.json() as BrowserSession;
}

export async function sendBrowserFrame(id: string, token: string, frame: Blob, signal: AbortSignal): Promise<void> {
  const response = await apiFetch(`/api/v1/cameras/${encodeURIComponent(id)}/webcam/frames`, {
    method: "POST", headers: { "Content-Type": "image/jpeg", "X-Webcam-Token": token },
    body: frame, signal
  });
  await requireSuccess(response);
}

export async function stopBrowserSession(id: string, token: string): Promise<void> {
  await apiFetch(`/api/v1/cameras/${encodeURIComponent(id)}/webcam/session`, {
    method: "DELETE", headers: { "X-Webcam-Token": token }, keepalive: true
  });
}
import { apiFetch } from "./client";
