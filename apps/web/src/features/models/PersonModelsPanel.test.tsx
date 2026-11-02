import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {PersonModelsPanel} from './PersonModelsPanel';

afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

it('uploads explicit YOLOX settings as a draft without activating it', async () => {
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => Response.json(options?.method === 'POST' ? {} : {items: [], max_upload_bytes: 536870912}));
  vi.stubGlobal('fetch', fetcher);
  render(<PersonModelsPanel />);
  await screen.findByText(/Chưa có model phát hiện người/);
  fireEvent.click(screen.getByText('Thêm model person · YOLOX'));
  fireEvent.change(screen.getByLabelText('File model ONNX'), {target: {files: [new File(['test'], 'yolox.onnx')]}});
  fireEvent.change(screen.getByLabelText('Kiểu đầu ra'), {target: {value: 'yolox_decoded'}});
  fireEvent.submit(screen.getByRole('button', {name: 'Kiểm tra & tải model'}).closest('form')!);
  await screen.findByText(/Đã kiểm tra và lưu bản nháp/);
  const post = fetcher.mock.calls.find(([, options]) => options?.method === 'POST')!;
  expect(post[0]).toBe('/api/v1/person-models');
  const data = post[1]!.body as FormData;
  expect(data.get('decode_mode')).toBe('yolox_decoded');
  expect(data.get('person_class_id')).toBe('0');
  expect(fetcher.mock.calls.some(([url]) => url.includes('/activate'))).toBe(false);
});

it('requires a successful worker warmup before allowing assignment', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({items: [{id:'v1',version:'v1',input_shape:[1,3,608,1088],decode_mode:'yolox_raw',person_class_id:0,deployments:[{id:'d1',runtime_state:'LOADING',status:'ACTIVATION_PENDING'}]}],max_upload_bytes:536870912})));
  render(<PersonModelsPanel />);
  await waitFor(() => expect(screen.getByRole('button', {name:'Áp dụng lên camera'}).hasAttribute('disabled')).toBe(true));
});

it('edits a draft and confirms deletion without triggering activation', async () => {
  const item = {id:'v1', version:'draft-v1', architecture:'YOLO26', input_shape:[1,3,640,640], confidence:0.3, deployments:[]};
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'DELETE' ? new Response(null, {status:204}) : Response.json({items:[item],max_upload_bytes:1024}));
  vi.stubGlobal('fetch', fetcher);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<PersonModelsPanel/>);
  fireEvent.click(await screen.findByRole('button', {name:'Sửa model'}));
  fireEvent.change(screen.getByLabelText('Tên phiên bản'), {target:{value:'front-door-v2'}});
  const editForm = screen.getByRole('button', {name:'Lưu model'}).closest('form')!;
  fireEvent.change(within(editForm).getByLabelText('Ngưỡng phát hiện'), {target:{value:'0.55'}});
  fireEvent.submit(screen.getByRole('button', {name:'Lưu model'}).closest('form')!);
  await screen.findByText('Đã lưu thông tin model.');
  const patch = fetcher.mock.calls.find(([,init])=>init?.method==='PATCH');
  expect(patch?.[0]).toBe('/api/v1/person-models/v1');
  expect(JSON.parse(String(patch?.[1]?.body))).toEqual({version:'front-door-v2',confidence:0.55});
  fireEvent.click(screen.getByRole('button', {name:'Xóa model'}));
  expect(fetcher.mock.calls.some(([,init])=>init?.method==='DELETE')).toBe(false);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', {name:'Xóa model'}));
  await screen.findByText('Đã xóa model khỏi danh sách.');
  expect(fetcher.mock.calls.some(([url,init])=>url==='/api/v1/person-models/v1'&&init?.method==='DELETE')).toBe(true);
  expect(fetcher.mock.calls.some(([url])=>url.includes('/activate'))).toBe(false);
});
