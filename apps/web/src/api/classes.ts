import { requestJson } from "./client";

export interface Classroom {
  id: string;
  name: string;
  course: string;
  section: string;
  term: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClassPerson {
  id: string;
  full_name: string;
  student_id: string | null;
}

export interface ClassSession {
  id: string;
  classroom_id: string;
  camera_id: string;
  label: string;
  starts_at: string;
  ends_at: string;
  timezone_id: string;
  state: "SCHEDULED" | "DONE" | "CANCELLED";
  done: boolean;
  created_by: string;
  created_at: string;
}

export interface AttendanceEntry {
  person_id: string;
  full_name: string;
  student_id: string | null;
  status: "PRESENT" | "UNKNOWN_SYSTEM";
  recognized_at: string | null;
  camera_id: string | null;
}

export interface SessionAttendance {
  classroom_id: string;
  session_id: string;
  timezone_id: string;
  starts_at: string;
  ends_at: string;
  items: AttendanceEntry[];
}

export interface FaceReviewImage {
  id: string;
  person_id: string;
  original_filename: string;
  mime_type: "image/jpeg";
  width: number;
  height: number;
  review_state: "PENDING_REVIEW" | "APPROVED" | "REJECTED";
  source: "MANUAL_UPLOAD" | "CAMERA_CAPTURE";
  created_at: string;
  content_url: string;
  person_name?: string | null;
  student_id?: string | null;
  person_number?: number | null;
}

export interface FaceReviewResult extends FaceReviewImage {}

export interface Account {
  id: string;
  email: string;
  role: "ADMIN" | "TEACHER" | "STUDENT";
  person_id: string | null;
  disabled: boolean;
}
export interface TeacherCamera { id:string; name:string; source_type:string; }

export function listTeacherCameras(): Promise<TeacherCamera[]> {
  return requestJson<{items:TeacherCamera[]}>('/api/v1/teacher/cameras').then(value=>value.items);
}

export function listTeacherClasses(): Promise<Classroom[]> {
  return requestJson<{ items: Classroom[] }>("/api/v1/teacher/classes").then(value => value.items);
}

export function listTeacherSessions(classId: string): Promise<ClassSession[]> {
  return requestJson<{ items: ClassSession[] }>(`/api/v1/teacher/classes/${encodeURIComponent(classId)}/sessions`).then(value => value.items);
}

export function createTeacherSession(classId: string, input: {
  camera_id: string; label: string; starts_at: string; ends_at: string; timezone_id: string;
}): Promise<ClassSession> {
  return requestJson(`/api/v1/teacher/classes/${encodeURIComponent(classId)}/sessions`, {
    method: "POST", body: JSON.stringify(input),
  });
}

export function getSessionAttendance(classId: string, sessionId: string): Promise<SessionAttendance> {
  return requestJson(`/api/v1/teacher/classes/${encodeURIComponent(classId)}/sessions/${encodeURIComponent(sessionId)}/attendance`);
}

export function completeTeacherSession(classId: string, sessionId: string): Promise<ClassSession> {
  return requestJson(`/api/v1/teacher/classes/${encodeURIComponent(classId)}/sessions/${encodeURIComponent(sessionId)}/complete`, { method: "POST" });
}

export function listPendingFaceReviews(): Promise<FaceReviewImage[]> {
  return requestJson<{ items: FaceReviewImage[] }>("/api/v1/teacher/face-images/pending").then(value => value.items);
}

export function reviewFaceImage(imageId: string, decision: "APPROVE" | "REJECT"): Promise<FaceReviewResult> {
  return requestJson(`/api/v1/teacher/face-images/${encodeURIComponent(imageId)}/review`, {
    method: "POST", body: JSON.stringify({ decision }),
  });
}

export function listAdminPendingFaceReviews(signal?: AbortSignal): Promise<FaceReviewImage[]> {
  return requestJson<{ items: FaceReviewImage[] }>("/api/v1/admin/face-images/pending", {signal}).then(value => value.items);
}

export function reviewAdminFaceImage(imageId: string, decision: "APPROVE" | "REJECT"): Promise<FaceReviewResult> {
  return requestJson(`/api/v1/admin/face-images/${encodeURIComponent(imageId)}/review`, {
    method: "POST", body: JSON.stringify({ decision }),
  });
}

export function listAccounts(): Promise<Account[]> {
  return requestJson<{ items: Account[] }>("/api/v1/admin/accounts").then(value => value.items);
}

export function createAccount(input: {
  email: string; password: string; role: Account["role"]; person_id?: string;
}): Promise<Account> {
  return requestJson("/api/v1/admin/accounts", { method: "POST", body: JSON.stringify(input) });
}

export function disableAccount(userId: string): Promise<void> {
  return requestJson<void>(`/api/v1/admin/accounts/${encodeURIComponent(userId)}/disable`, { method: "POST" });
}

export function listAdminClasses(): Promise<Classroom[]> {
  return requestJson<{ items: Classroom[] }>("/api/v1/admin/classrooms").then(value => value.items);
}

export function createAdminClass(input: Pick<Classroom, "name" | "course" | "section" | "term">): Promise<Classroom> {
  return requestJson("/api/v1/admin/classrooms", { method: "POST", body: JSON.stringify(input) });
}

export function listClassTeachers(classId: string): Promise<Array<{teacher_id: string}>> {
  return requestJson<{ items: Array<{teacher_id: string}> }>(`/api/v1/admin/classrooms/${encodeURIComponent(classId)}/teachers`).then(value => value.items);
}

export function assignClassTeacher(classId: string, teacherId: string): Promise<void> {
  return requestJson(`/api/v1/admin/classrooms/${encodeURIComponent(classId)}/teachers`, {
    method: "POST", body: JSON.stringify({ teacher_id: teacherId }),
  }).then(() => undefined);
}

export function listClassMembers(classId: string): Promise<ClassPerson[]> {
  return requestJson<{ items: ClassPerson[] }>(`/api/v1/admin/classrooms/${encodeURIComponent(classId)}/members`).then(value => value.items);
}

export function addClassMember(classId: string, personId: string): Promise<void> {
  return requestJson(`/api/v1/admin/classrooms/${encodeURIComponent(classId)}/members`, {
    method: "POST", body: JSON.stringify({ person_id: personId }),
  }).then(() => undefined);
}
