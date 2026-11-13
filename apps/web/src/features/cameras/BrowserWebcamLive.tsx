import { useCallback, useEffect, useId, useRef, useState } from "react";

import { sendBrowserFrame, startBrowserSession, stopBrowserSession } from "../../api/browserCamera";
import type { CameraPublic } from "../../api/contracts";

export function BrowserWebcamLive({ camera }: { camera: CameraPublic }) {
  const video = useRef<HTMLVideoElement>(null);
  const media = useRef<MediaStream | null>(null);
  const session = useRef<{ cameraId: string; token: string } | null>(null);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const upload = useRef<AbortController | null>(null);
  const cleanupPromise = useRef(Promise.resolve());
  const [phase, setPhase] = useState<"off" | "opening" | "live">("off");
  const [sent, setSent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const deviceSelectId = useId();
  const deviceScan = useRef(0);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [scanning, setScanning] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);

  const refreshDevices = useCallback(async (requestPermission = false) => {
    const scan = ++deviceScan.current;
    setScanning(true); setDeviceError(null);
    try {
      if (!navigator.mediaDevices?.enumerateDevices || window.isSecureContext === false) {
        throw new Error("Chọn webcam cần địa chỉ localhost hoặc HTTPS và trình duyệt hỗ trợ camera.");
      }
      if (requestPermission) {
        const permissionStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        permissionStream.getTracks().forEach(track => track.stop());
        if (scan !== deviceScan.current) return;
      }
      const available = await navigator.mediaDevices.enumerateDevices();
      if (scan !== deviceScan.current) return;
      setDevices(available.filter(device => device.kind === "videoinput" && device.deviceId));
    } catch (reason) {
      if (scan !== deviceScan.current) return;
      setDeviceError(reason instanceof DOMException && reason.name === "NotAllowedError"
        ? "Hãy cho phép truy cập camera trong trình duyệt rồi làm mới danh sách."
        : reason instanceof Error ? reason.message : "Không đọc được danh sách webcam.");
    } finally {
      if (scan === deviceScan.current) setScanning(false);
    }
  }, []);

  useEffect(() => {
    setDeviceId("");
    void refreshDevices();
    const changed = () => { void refreshDevices(); };
    navigator.mediaDevices?.addEventListener?.("devicechange", changed);
    return () => {
      deviceScan.current += 1;
      navigator.mediaDevices?.removeEventListener?.("devicechange", changed);
    };
  }, [camera.id, refreshDevices]);

  const release = useCallback(() => {
    generation.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    upload.current?.abort(); upload.current = null;
    media.current?.getTracks().forEach(track => track.stop()); media.current = null;
    if (video.current) video.current.srcObject = null;
    const previous = session.current; session.current = null;
    const deletion = previous ? stopBrowserSession(previous.cameraId, previous.token).catch(() => {}) : Promise.resolve();
    cleanupPromise.current = Promise.all([cleanupPromise.current, deletion]).then(() => {});
    return cleanupPromise.current;
  }, []);

  const stop = useCallback(() => { void release(); setPhase("off"); }, [release]);

  useEffect(() => {
    setPhase("off"); setError(null);
    return () => { void release(); };
  }, [camera.id, camera.revision, camera.enabled, camera.lifecycle, release]);

  useEffect(() => {
    const hidden = () => { if (document.visibilityState === "hidden") stop(); };
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [stop]);

  async function start() {
    setError(null); setSent(0); setPhase("opening");
    if (!navigator.mediaDevices?.getUserMedia || window.isSecureContext === false) {
      setError("Webcam cần trình duyệt hỗ trợ và địa chỉ localhost hoặc HTTPS."); setPhase("off"); return;
    }
    const request = generation.current;
    try {
      await cleanupPromise.current;
      if (request !== generation.current) return;
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "user" }),
          width: { ideal: 1280 }, height: { ideal: 720 },
          frameRate: { ideal: Math.max(1, Math.min(30, camera.display_fps ?? 15)) }
        }, audio: false
      });
      if (request !== generation.current) { stream.getTracks().forEach(track => track.stop()); return; }
      media.current = stream;
      void refreshDevices();
      stream.getTracks().forEach(track => track.addEventListener("ended", () => {
        if (request !== generation.current) return;
        stop(); setError("Webcam đã ngắt kết nối. Hãy bắt đầu lại để tiếp tục.");
      }, { once: true }));
      if (!video.current) { await release(); return; }
      video.current.srcObject = stream;
      await video.current.play();
      if (request !== generation.current) return;
      const created = await startBrowserSession(camera.id);
      if (request !== generation.current) {
        await stopBrowserSession(camera.id, created.token).catch(() => {}); return;
      }
      session.current = { cameraId: camera.id, token: created.token };
      setPhase("live");
      const canvas = document.createElement("canvas");
      const interval = 1000 / Math.max(1, Math.min(30, camera.display_fps ?? 15));

      const sendNext = async () => {
        if (request !== generation.current) return;
        const started = performance.now();
        const element = video.current;
        try {
          if (element?.videoWidth && element.videoHeight) {
            const scale = Math.min(1, 1280 / element.videoWidth, 720 / element.videoHeight);
            canvas.width = Math.max(1, Math.floor(element.videoWidth * scale));
            canvas.height = Math.max(1, Math.floor(element.videoHeight * scale));
            const context = canvas.getContext("2d");
            if (!context) throw new Error("Không đọc được hình ảnh webcam.");
            context.drawImage(element, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/jpeg", 0.75));
            if (request !== generation.current) return;
            if (!blob || blob.size > 1024 * 1024) throw new Error("Không tạo được khung hình phù hợp. Hãy bắt đầu lại webcam.");
            const controller = new AbortController(); upload.current = controller;
            await sendBrowserFrame(camera.id, created.token, blob, controller.signal);
            if (request !== generation.current) return;
            upload.current = null;
            setSent(value => value + 1);
          }
          if (request === generation.current) timer.current = setTimeout(() => { void sendNext(); }, Math.max(0, interval - (performance.now() - started)));
        } catch (reason) {
          if (request !== generation.current) return;
          stop();
          setError(reason instanceof Error ? reason.message : "Kết nối webcam bị gián đoạn. Hãy bắt đầu lại.");
        }
      };
      void sendNext();
    } catch (reason) {
      if (request !== generation.current) return;
      stop();
      setError(reason instanceof DOMException && reason.name === "NotAllowedError"
        ? "Chưa được cấp quyền camera. Hãy cho phép webcam trong trình duyệt rồi thử lại."
        : reason instanceof DOMException && ["NotFoundError", "OverconstrainedError"].includes(reason.name)
          ? "Không tìm thấy webcam đã chọn. Hãy kết nối lại thiết bị, làm mới danh sách và chọn webcam khác."
        : reason instanceof Error ? reason.message : "Không mở được webcam.");
    }
  }

  const allowed = camera.source_type === "webcam" && camera.enabled && camera.lifecycle === "ACTIVE";
  return (
    <div className="browser-webcam-live">
      <p>Webcam của trình duyệt · Truyền hình tối đa {Math.max(1, Math.min(30, camera.display_fps ?? 15))} FPS · Sampling worker {camera.sampling_fps} FPS</p>
      <label htmlFor={deviceSelectId}>Thiết bị webcam / DroidCam</label>
      <select id={deviceSelectId} value={deviceId} disabled={phase !== "off" || scanning}
        onChange={event => setDeviceId(event.target.value)}>
        <option value="">Webcam mặc định của trình duyệt</option>
        {deviceId && !devices.some(device => device.deviceId === deviceId)
          && <option value={deviceId}>Thiết bị đã ngắt kết nối</option>}
        {devices.map((device, index) => <option key={device.deviceId} value={device.deviceId}>
          {device.label || `Webcam ${index + 1}`}
        </option>)}
      </select>
      <button type="button" disabled={phase !== "off" || scanning}
        onClick={() => { void refreshDevices(true); }}>Cấp quyền / làm mới webcam</button>
      {scanning && <p role="status">Đang tìm thiết bị webcam…</p>}
      <p className="registry-help">Kết nối DroidCam trên Windows, sau đó làm mới và chọn thiết bị cho từng camera.
        Dừng realtime trước khi đổi thiết bị. Nếu dùng nhiều điện thoại, mỗi nguồn cần xuất hiện riêng trong trình duyệt.</p>
      {deviceError && <p role="alert" className="registry-error">{deviceError}</p>}
      {!allowed && <p>Kích hoạt camera trước khi bắt đầu realtime.</p>}
      {phase === "off" ? <button type="button" className="button-primary" disabled={!allowed || scanning} onClick={start}>Bắt đầu realtime</button>
        : <button type="button" onClick={stop}>Dừng realtime</button>}
      {phase === "opening" && <p role="status">Đang kết nối webcam…</p>}
      <video ref={video} aria-label={`Webcam realtime ${camera.name}`} autoPlay muted playsInline hidden={phase !== "live"} />
      <p role="status">{phase === "live" ? `Đang truyền · Đã gửi ${sent} khung hình` : "Webcam đã dừng"}</p>
      <p className="registry-help">Xử lý nhận diện phụ thuộc model đã gán. FPS xử lý và trạng thái worker hiển thị phía trên.</p>
      {error && <p role="alert" className="registry-error">{error}</p>}
    </div>
  );
}
