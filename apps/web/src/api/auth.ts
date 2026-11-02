import { getCsrfToken, setCsrfToken } from "./client";

export type UserRole = "ADMIN" | "TEACHER" | "STUDENT";

export interface SessionUser {
  user_id: string;
  email: string;
  role: UserRole;
  person_id: string | null;
}

export interface SessionSnapshot {
  user: SessionUser;
  csrf_token: string;
  expires_at?: string;
}

async function authJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = body && typeof body === "object" && "detail" in body
      && typeof body.detail === "string" ? body.detail : undefined;
    if (response.status === 401) throw new Error(path.endsWith("/login") ? "Thông tin đăng nhập chưa đúng." : "AUTH_REQUIRED");
    throw new Error(detail ?? `Yêu cầu thất bại (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  const value = await response.json() as T;
  if (value && typeof value === "object" && "csrf_token" in value
    && typeof value.csrf_token === "string") {
    setCsrfToken(value.csrf_token);
  }
  return value;
}

export function getCurrentSession(): Promise<SessionSnapshot> {
  return authJson("/api/v1/auth/me");
}

export function signIn(email: string, password: string): Promise<SessionSnapshot> {
  return authJson("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function signOut(): Promise<void> {
  const csrfToken = getCsrfToken();
  return authJson<void>("/api/v1/auth/logout", {
    method: "POST",
    headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
  }).then(() => { setCsrfToken(null); });
}
