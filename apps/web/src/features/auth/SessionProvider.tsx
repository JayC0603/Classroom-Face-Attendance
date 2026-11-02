import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getCurrentSession, signIn, signOut, type SessionSnapshot, type SessionUser } from "../../api/auth";
import { setCsrfToken, setUnauthorizedHandler } from "../../api/client";

type SessionStatus = "loading" | "anonymous" | "authenticated" | "error";

interface SessionContextValue {
  status: SessionStatus;
  user: SessionUser | null;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  retry: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [user, setUser] = useState<SessionUser | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const clear = useCallback(() => {
    setCsrfToken(null);
    setUser(null);
    setExpiresAt(null);
    setError(null);
    setStatus("anonymous");
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(clear);
    return () => setUnauthorizedHandler(null);
  }, [clear]);

  useEffect(() => {
    let disposed = false;
    setStatus("loading");
    getCurrentSession().then((session: SessionSnapshot) => {
      if (disposed) return;
      setUser(session.user);
      setExpiresAt(session.expires_at ?? null);
      setStatus("authenticated");
      setError(null);
    }).catch((reason: unknown) => {
      if (disposed) return;
      if (reason instanceof Error && reason.message !== "AUTH_REQUIRED") {
        setError(reason.message);
        setStatus("error");
      } else {
        clear();
      }
    });
    return () => { disposed = true; };
  }, [attempt, clear]);

  useEffect(() => {
    if (status !== "authenticated" || !expiresAt) return;
    const remaining = Date.parse(expiresAt) - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) { clear(); return; }
    const timer = window.setTimeout(clear, Math.min(remaining, 2_147_000_000));
    return () => window.clearTimeout(timer);
  }, [status, expiresAt, clear]);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const session = await signIn(email, password);
      setUser(session.user);
      setExpiresAt(session.expires_at ?? null);
      setStatus("authenticated");
    } catch (reason) {
      setUser(null);
      setStatus("anonymous");
      const message = reason instanceof Error ? reason.message : "Đăng nhập chưa thành công.";
      setError(message);
      throw reason;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await signOut();
    } finally {
      clear();
    }
  }, [clear]);

  const value = useMemo<SessionContextValue>(() => ({
    status,
    user,
    error,
    signIn: login,
    signOut: logout,
    retry: () => setAttempt(value => value + 1),
  }), [status, user, error, login, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
