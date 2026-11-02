import { useState, type FormEvent } from "react";
import fptLogo from "../../assets/fpt-university-logo.png";
import { ThemeToggle } from "../../ui/ThemeToggle";

export function LoginPage({
  onSignIn,
  error,
  onRetry,
}: {
  onSignIn: (email: string, password: string) => Promise<void>;
  error: string | null;
  onRetry: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await onSignIn(email.trim(), password);
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : "Không thể đăng nhập.");
    } finally {
      setBusy(false);
    }
  }

  return <main className="login-page">
    <section className="login-card" aria-labelledby="login-title">
      <div className="login-brand"><img src={fptLogo} alt="Trường Đại học FPT" /><ThemeToggle/></div>
      <p className="eyebrow">KHÔNG GIAN HỌC TẬP</p>
      <h1 id="login-title">Đăng nhập</h1>
      <p className="login-intro">Đăng nhập bằng tài khoản do quản trị viên cấp để tiếp tục.</p>
      {(formError || error) && <p className="error" role="alert">{formError ?? error}</p>}
      {error && !formError && <button type="button" className="secondary-button" onClick={onRetry}>Thử kết nối lại</button>}
      <form onSubmit={submit}>
        <label>Email<input type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} /></label>
        <label>Mật khẩu<input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={busy}>{busy ? "Đang xác thực…" : "Đăng nhập"}</button>
      </form>
      <p className="login-footnote">Phiên đăng nhập được bảo vệ và tự kết thúc khi bạn đăng xuất.</p>
    </section>
  </main>;
}
