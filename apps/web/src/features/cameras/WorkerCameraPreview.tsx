import { useEffect, useState } from 'react';
import { fetchCameraPreview } from '../../api/cameraPreview';
import type { CameraPublic } from '../../api/contracts';

export function WorkerCameraPreview({ camera }: { camera: CameraPublic }) {
  const [image, setImage] = useState<string | null>(null);
  const [message, setMessage] = useState('Đang chờ hình từ worker…');
  const active = camera.enabled && camera.lifecycle === 'ACTIVE';

  useEffect(() => {
    setImage(null);
    setMessage('Đang chờ hình từ worker…');
    if (!active) return;
    let disposed = false;
    let currentUrl: string | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | null = null;

    function clearImage() {
      if (currentUrl) URL.revokeObjectURL(currentUrl);
      currentUrl = null;
      if (!disposed) setImage(null);
    }

    async function poll() {
      if (disposed) return;
      const started = performance.now();
      if (document.visibilityState === 'hidden') {
        clearImage();
        timer = setTimeout(() => void poll(), 500);
        return;
      }
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 2000);
      try {
        const frame = await fetchCameraPreview(camera.id, camera.revision, request.signal);
        if (disposed) return;
        clearImage();
        if (frame) {
          currentUrl = URL.createObjectURL(frame);
          setImage(currentUrl);
          setMessage(`Hình trực tiếp từ worker · xem trước tối đa ${camera.display_fps ?? 15} FPS`);
        } else {
          setMessage('Đang chờ hình từ worker…');
        }
      } catch {
        if (!disposed) {
          clearImage();
          setMessage('Chưa nhận được hình. Đang kết nối lại…');
        }
      } finally {
        clearTimeout(timeout);
        request = null;
        const interval = 1000 / Math.max(1, Math.min(30, camera.display_fps ?? 15));
        if (!disposed) timer = setTimeout(() => void poll(), Math.max(0, interval - (performance.now() - started)));
      }
    }

    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      request?.abort();
      clearImage();
    };
  }, [camera.id, camera.revision, camera.display_fps, active]);

  if (!active) return null;
  return <figure className="worker-camera-preview">
    {image && <img src={image} alt={`Camera ${camera.name}`} />}
    <figcaption role="status">{message}</figcaption>
  </figure>;
}
