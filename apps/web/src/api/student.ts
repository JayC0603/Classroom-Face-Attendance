import { requestJson, apiFetch } from "./client";
import type { FaceImagePublic, UploadResult } from "./registry";

export interface StudentProfile { id: string; full_name: string; student_id: string | null; recognition_ready: boolean; }
export interface StudentAttendance { id: string; attendance_date: string; first_seen_at: string | null; last_seen_at: string | null; exited_at: string | null; camera_id: string | null; camera_name: string | null; }
export interface StudentEvent { id: string; camera_id: string | null; camera_name: string | null; recognized_at: string; attendance_date: string; }

export function getMyProfile(): Promise<StudentProfile> { return requestJson("/api/v1/me/profile"); }
export function listMyFaceImages(): Promise<FaceImagePublic[]> { return requestJson<{items:FaceImagePublic[]}>("/api/v1/me/face-images").then(value=>value.items); }
export async function uploadMyFaceImages(files: File[]): Promise<UploadResult[]> {
  const body=new FormData(); files.forEach(file=>body.append("files",file)); body.set("source","MANUAL_UPLOAD");
  return requestJson<{results:UploadResult[]}>("/api/v1/me/face-images",{method:"POST",body}).then(value=>value.results);
}
export function myFaceImageUrl(id:string): string { return `/api/v1/me/face-images/${encodeURIComponent(id)}/content`; }
export function listMyAttendance(): Promise<StudentAttendance[]> { return requestJson<{items:StudentAttendance[]}>("/api/v1/me/attendance").then(value=>value.items); }
export function listMyEvents(): Promise<StudentEvent[]> { return requestJson<{items:StudentEvent[]}>("/api/v1/me/events").then(value=>value.items); }
export function myEventEvidenceUrl(id:string): string { return `/api/v1/me/events/${encodeURIComponent(id)}/evidence`; }
export async function loadPrivateImage(url:string): Promise<string> {
  const response=await apiFetch(url,{cache:"no-store"}); if(!response.ok) throw new Error("Không tải được ảnh riêng tư");
  return URL.createObjectURL(await response.blob());
}
