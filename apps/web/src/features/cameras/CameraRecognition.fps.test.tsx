import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { CameraPublic } from '../../api/contracts';
import { CameraRecognition } from './CameraRecognition';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('uses sampling cadence in compact mode without adding response time to every interval', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(() => new Promise<Response>(resolve => {
    setTimeout(() => resolve(Response.json({ state: 'READY', faces: [] })), 50);
  }));
  vi.stubGlobal('fetch', fetch);
  const camera = { id: 'cam', revision: 'r1', lifecycle: 'ACTIVE', enabled: true,
    sampling_fps: 10, display_fps: 30 } as CameraPublic;
  render(<CameraRecognition camera={camera} compact />);
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(99); });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(101); });
  expect(fetch).toHaveBeenCalledTimes(3);
});

it('keeps one recognition request in flight when the API is slower than sampling', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(() => new Promise<Response>(() => {}));
  vi.stubGlobal('fetch', fetch);
  const camera = { id: 'cam', revision: 'r1', lifecycle: 'ACTIVE', enabled: true,
    sampling_fps: 30, display_fps: 30 } as CameraPublic;
  render(<CameraRecognition camera={camera} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(fetch).toHaveBeenCalledTimes(1);
});
