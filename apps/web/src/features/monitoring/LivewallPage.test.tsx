import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {LivewallPage} from './LivewallPage';
import type {CameraPublic} from '../../api/contracts';

const cameras: CameraPublic[] = Array.from({length: 10}, (_, index) => ({
  id: `cam-${index}`, name: `Camera ${index}`, source_type: 'rtsp', codec: 'AUTO',
  rtsp_url: null, enabled: true, sampling_fps: 5, lifecycle: 'ACTIVE', revision: `rev-${index}`,
}));
afterEach(() => {cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

it('opens four independent feeds, caps selection at nine, and preserves feeds while focusing', async () => {
  const requests: Array<{url: string; signal: AbortSignal}> = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (url === '/api/v1/cameras') return Promise.resolve(Response.json({items: cameras}));
    requests.push({url, signal: options.signal as AbortSignal});
    return new Promise<Response>(() => {});
  }));
  const {unmount} = render(<LivewallPage statuses={[]}/>);
  await screen.findByText('4 camera trên Livewall');
  await waitFor(() => expect(requests).toHaveLength(4));
  expect(requests[0].url).toContain('/cam-0/recognition?revision=rev-0');
  fireEvent.click(screen.getByRole('button', {name:'Phóng lớn Camera 0'}));
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(requests.some(request => request.signal.aborted)).toBe(false);
  fireEvent.keyDown(window, {key:'Escape'});
  expect(screen.getAllByRole('article')).toHaveLength(4);
  fireEvent.click(screen.getByText('Chọn camera hiển thị (4/9)'));
  for (let index = 4; index < 9; index++) fireEvent.click(screen.getByRole('checkbox', {name: new RegExp(`Camera ${index}`)}));
  expect(screen.getByRole('checkbox', {name: /Camera 9/}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', {name: /Camera 0/}));
  expect(requests[0].signal.aborted).toBe(true);
  unmount();
  expect(requests.every(request => request.signal.aborted)).toBe(true);
});

it('keeps camera failures separate and does not poll a disabled camera', async () => {
  sessionStorage.setItem('livewall-cameras', JSON.stringify(['cam-0','cam-1','cam-2']));
  const fetcher = vi.fn(async (url: string) => {
    if (url === '/api/v1/cameras') return Response.json({items: cameras.slice(0,3).map((camera,index) => ({...camera,enabled:index !== 2}))});
    if (url.includes('/cam-0/')) return new Response(null, {status:503});
    return Response.json({state:'READY',faces:[],persons:[],preview:'data:image/jpeg;base64,test'});
  });
  vi.stubGlobal('fetch', fetcher);
  render(<LivewallPage statuses={[]}/>);
  await screen.findByAltText('Camera nhận diện Camera 1');
  expect(within(screen.getByRole('article', {name:'Ô camera Camera 0'})).getByText('Mất kết nối nhận diện. Đang thử lại…')).toBeTruthy();
  expect(fetcher.mock.calls.some(([url]) => url.includes('/cam-2/'))).toBe(false);
  expect(screen.getByText('Camera đang tắt hoặc chưa kích hoạt.')).toBeTruthy();
});

it('shows onboarding without falsely reporting an empty list on network failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => {throw new Error('offline');}));
  render(<LivewallPage statuses={[]}/>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('Chưa có camera để quan sát')).toBeNull();
});
