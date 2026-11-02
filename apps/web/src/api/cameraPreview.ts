export async function fetchCameraPreview(cameraId: string, revision: string, signal: AbortSignal): Promise<Blob | null> {
  const response = await apiFetch(`/api/v1/cameras/${encodeURIComponent(cameraId)}/preview?revision=${encodeURIComponent(revision)}`, {
    signal, cache: 'no-store'
  });
  if (response.status === 204) return null;
  if (!response.ok) throw new Error('PREVIEW_UNAVAILABLE');
  const image = await response.blob();
  if (image.type !== 'image/jpeg') throw new Error('INVALID_PREVIEW');
  return image;
}
import { apiFetch } from "./client";
