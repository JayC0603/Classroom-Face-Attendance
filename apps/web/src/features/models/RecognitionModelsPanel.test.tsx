import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {ModelsPanel} from './ModelsPanel';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('does not invent zero reference counts while the service is processing', async()=> {
  vi.stubGlobal('fetch',vi.fn(async()=>Response.json({state:'PROCESSING',model_names:['YuNet','SFace']})));
  render(<ModelsPanel />);
  await screen.findByText('Đang chuẩn bị model và ảnh mẫu…');
  expect(screen.getByRole('link', {name: /M\u1edf h\u1ed3 s\u01a1 khu\u00f4n m\u1eb7t \u0111\u1ec3 t\u1ea3i \u1ea3nh/}).getAttribute('href')).toBe('#people');
  expect(screen.queryByText(/0 hồ sơ/)).toBeNull();
});

it('uses installed comparison models without requiring model uploads or embedding setup', async () => {
  vi.stubGlobal('fetch',vi.fn(async () => Response.json({state:'READY',model_names:['YuNet','SFace'],reference_people:2,reference_images:3,images:[]})));
  render(<ModelsPanel />);
  expect(await screen.findByText('Sẵn sàng so sánh')).toBeTruthy();
  expect(screen.getByText(/2 hồ sơ · 3 ảnh mẫu dùng được/)).toBeTruthy();
  expect(screen.queryByText('Face Embedding')).toBeNull();
  expect(screen.queryByText('ONNX artifact')).toBeNull();
});
