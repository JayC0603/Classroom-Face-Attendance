import { useCallback, useEffect, useRef, useState } from "react";

interface Props {
  disabled: boolean;
  remaining: number;
  onCapture(file: File): void;
}

function cameraError(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Chưa được cấp quyền truy cập webcam. Hãy cho phép camera trong trình duyệt rồi thử lại.";
  }
  if (name === "NotFoundError") return "Không tìm thấy webcam trên thiết bị này.";
  if (name === "NotReadableError") return "Không mở được webcam. Hãy kiểm tra camera có đang được ứng dụng khác sử dụng không.";
  return "Không mở được webcam. Kiểm tra kết nối camera và thử lại.";
}

export function WebcamCapture({ disabled, remaining, onCapture }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const [status, setStatus] = useState<"off" | "opening" | "on">("off");
  const [ready, setReady] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const release = useCallback(() => {
    generation.current += 1;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
    if (video.current) video.current.srcObject = null;
  }, []);

  const stop = useCallback(() => {
    release(); setStatus("off"); setReady(false); setCapturing(false);
  }, [release]);

  useEffect(() => release, [release]);
  useEffect(() => { if (disabled) stop(); }, [disabled, stop]);

  async function start() {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || window.isSecureContext === false) {
      setError("Trình duyệt không hỗ trợ webcam tại địa chỉ này. Hãy mở ứng dụng bằng localhost hoặc HTTPS.");
      return;
    }
    release(); setReady(false); setStatus("opening");
    const request = generation.current;
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false
      });
      if (request !== generation.current) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const ended = () => {
        if (request !== generation.current) return;
        stop(); setError("Webcam đã ngắt kết nối. Bạn có thể bật lại để tiếp tục.");
      };
      media.getTracks().forEach(track => track.addEventListener("ended", ended, { once: true }));
      if (video.current) {
        video.current.srcObject = media;
        await video.current.play();
      }
      if (request === generation.current) setStatus("on");
    } catch (reason) {
      if (request !== generation.current) return;
      stop(); setError(cameraError(reason));
    }
  }

  async function capture() {
    const element = video.current;
    if (disabled || capturing || remaining < 1 || !element || !ready || !stream.current) return;
    const request = generation.current;
    setError(null); setCapturing(true);
    try {
      const canvas = document.createElement("canvas");
      canvas.width = element.videoWidth; canvas.height = element.videoHeight;
      const context = canvas.getContext("2d");
      if (!context || !canvas.width || !canvas.height) throw new Error("FRAME_UNAVAILABLE");
      context.drawImage(element, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/jpeg", 0.9));
      if (request !== generation.current) return;
      if (!blob || blob.size > 10 * 1024 * 1024) throw new Error("CAPTURE_FAILED");
      onCapture(new File([blob], `webcam-${Date.now()}-${crypto.randomUUID()}.jpg`, { type: "image/jpeg" }));
    } catch {
      if (request === generation.current) setError("Không chụp được ảnh. Hãy chờ hình ảnh webcam ổn định rồi thử lại.");
    } finally {
      if (request === generation.current) setCapturing(false);
    }
  }

  return (
    <div className="webcam-capture">
      <p className="registry-help">Bật webcam, chụp ảnh rồi bấm lưu để thêm vào hồ sơ. Âm thanh không được thu.</p>
      {status === "off" ? <button type="button" disabled={disabled} onClick={start}>Bật webcam</button>
        : <button type="button" onClick={stop}>Tắt webcam</button>}
      {status === "opening" && <p role="status">Đang mở webcam…</p>}
      <video ref={video} aria-label="Hình ảnh webcam" autoPlay muted playsInline hidden={status !== "on"}
        onLoadedData={() => setReady(!!video.current?.videoWidth && !!video.current?.videoHeight)}
        onCanPlay={() => setReady(!!video.current?.videoWidth && !!video.current?.videoHeight)} />
      {status === "on" && <button type="button" disabled={disabled || !ready || capturing || remaining < 1}
        onClick={capture}>{capturing ? "Đang chụp…" : "Chụp ảnh"}</button>}
      {remaining === 0 && <p role="status">Đã chọn đủ 10 ảnh. Hãy lưu trước khi chụp thêm.</p>}
      {error && <p role="alert" className="registry-error">{error}</p>}
    </div>
  );
}
