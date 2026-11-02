import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkerCameraPreview } from './WorkerCameraPreview';
import type { CameraPublic } from '../../api/contracts';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows actual worker frames, scopes requests to revision, and releases image URLs', async () => {
  const revoke = vi.fn();
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: revoke });
  const fetch = vi.fn(async (_input: RequestInfo | URL) => ({
    status: 200, ok: true, blob: async () => new Blob(['jpeg'], { type: 'image/jpeg' })
  } as Response));
  vi.stubGlobal('fetch', fetch);
  const camera = { id: 'cam', name: 'Local', revision: 'r1', source_type: 'local_webcam',
    enabled: true, lifecycle: 'ACTIVE' } as CameraPublic;
  const { unmount } = render(<WorkerCameraPreview camera={camera} />);
  const image = await screen.findByAltText('Camera Local');
  await waitFor(() => expect(image.getAttribute('src')).toBe('blob:preview'));
  expect(String(fetch.mock.calls[0][0])).toContain('revision=r1');
  unmount();
  expect(revoke).toHaveBeenCalledWith('blob:preview');
});

it('does not fetch an inactive camera', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  render(<WorkerCameraPreview camera={{id:'cam', enabled:false, lifecycle:'ACTIVE'} as CameraPublic} />);
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole('img')).toBeNull();
});
