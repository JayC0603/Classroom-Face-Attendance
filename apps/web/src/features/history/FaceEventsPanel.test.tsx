import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {FaceEventsPanel} from './FaceEventsPanel';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('shows persisted entry crops and stranger alerts with separate evidence states', async () => {
  const fetch = vi.fn(async (_input: RequestInfo | URL) => Response.json([
    {id:'entry',kind:'FACE_CAPTURED',camera_name:'Door',recognized_at:'2026-11-02T10:00:00Z',evidence_status:'READY',evidence_key:'face-entry.jpg'},
    {id:'alert',kind:'UNKNOWN_ALERT',camera_name:'Door',recognized_at:'2026-11-02T10:00:01Z',consecutive_unknown:5,evidence_status:'FAILED',evidence_key:null}
  ]));
  vi.stubGlobal('fetch', fetch);
  render(<FaceEventsPanel cameraId="door" />);
  expect(await screen.findByText('Cảnh báo người lạ')).toBeTruthy();
  expect(screen.getByAltText('Khuôn mặt khi vào ROI').getAttribute('src')).toContain('face-entry.jpg');
  expect(screen.getByText('5 frame liên tiếp không khớp ảnh đăng ký.')).toBeTruthy();
  expect(screen.getByText(/Không lưu được ảnh; sự kiện vẫn được ghi nhận/)).toBeTruthy();
  expect(String(fetch.mock.calls[0][0])).toContain('camera_id=door');
});
