import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CameraPanel } from "./CameraPanel";
import type { CameraPublic } from "../../api/contracts";

const camera: CameraPublic = {
  id: "camera-1",
  name: "Entrance",
  source_type: "rtsp",
  rtsp_url: "rtsp://***:***@camera.local/live",
  codec: "AUTO",
  enabled: true,
  sampling_fps: 5,
  lifecycle: "DRAFT",
  revision: "revision-1"
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CameraPanel", () => {
  it('filters cameras by name and preserves unsaved edits when cancellation is declined', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({items:[camera,{...camera,id:'camera-2',name:'Library'}]})));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]}/>);
    await screen.findByText('Library');
    await user.type(screen.getByLabelText('Tìm camera'), 'Entrance');
    expect(screen.queryByRole('heading',{name:'Library'})).toBeNull();
    await user.click(screen.getByRole('button',{name:'Chỉnh sửa'}));
    await user.click(screen.getByRole('button',{name:'Hủy'}));
    expect(confirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button',{name:'Chỉnh sửa'}));
    await user.type(screen.getByLabelText('Tên', {exact:true}), ' edited');
    await user.click(screen.getByRole('button',{name:'Hủy'}));
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByDisplayValue('Entrance edited')).toBeTruthy();
  });
  it('requires confirmation before deleting a camera with its loaded revision', async () => {
    const fetcher=vi.fn(async (_input:RequestInfo|URL,init?:RequestInit)=>init?.method==='DELETE'?new Response(null,{status:204}):Response.json({items:[camera]}));
    vi.stubGlobal('fetch',fetcher);
    const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
    const user=userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]}/>);
    await user.click(await screen.findByRole('button',{name:'Xóa camera'}));
    expect(fetcher.mock.calls.some(([,init])=>init?.method==='DELETE')).toBe(false);
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole('button',{name:'Xóa camera'}));
    await screen.findByText('Đã xóa camera.');
    expect(fetcher.mock.calls.some(([url,init])=>String(url).includes('expected_revision=revision-1')&&init?.method==='DELETE')).toBe(true);
  });
  it("starts a disabled worker camera and activates it for live detection", async () => {
    const requests: Array<{url:string;init?:RequestInit}> = [];
    vi.stubGlobal('fetch',vi.fn(async (input:RequestInfo | URL,init?:RequestInit) => {
      requests.push({url:String(input),init});
      return Response.json({items:[{...camera,source_type:'local_webcam',device_index:0,enabled:false}]});
    }));
    const user=userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await user.click(await screen.findByRole('button',{name:'Bật camera và nhận diện'}));
    await waitFor(() => expect(requests.some(item=>item.url.endsWith('/activate') && item.init?.method==='POST')).toBe(true));
    const patch=requests.find(item=>item.init?.method==='PATCH');
    expect(JSON.parse(String(patch?.init?.body))).toEqual({enabled:true});
  });
  it("offers all three sources and submits a worker webcam device without an RTSP URL", async () => {
    const requests: Array<RequestInit | undefined> = [];
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(init);
      return init?.method === "POST" ? Response.json({
        ...camera, source_type: "local_webcam", device_index: 2, rtsp_url: null
      }, { status: 201 }) : Response.json({ items: [] });
    }));
    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    const source = screen.getByLabelText("Nguồn");
    expect(screen.getByRole("option", { name: "RTSP" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Webcam trình duyệt" })).toBeTruthy();
    await user.selectOptions(source, "local_webcam");
    expect(screen.queryByLabelText("Địa chỉ RTSP")).toBeNull();
    await user.type(screen.getByLabelText("Tên camera"), "Worker webcam");
    const index = screen.getByLabelText("Số thiết bị webcam");
    await user.clear(index);
    await user.type(index, "2");
    await user.click(screen.getByRole("button", { name: "Lưu camera" }));
    await waitFor(() => {
      const request = requests.find(item => item?.method === "POST");
      const body = JSON.parse(String(request?.body));
      expect(body.source_type).toBe("local_webcam");
      expect(body.device_index).toBe(2);
      expect(body.rtsp_url).toBeUndefined();
    });
  });
  it("explains a rejected webcam save and keeps the editor open", async () => {
    const webcam = { ...camera, source_type: "webcam", rtsp_url: null };
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => (
      init?.method === "PATCH" ? Response.json({
        detail: "request validation failed", error_category: "VALIDATION_ERROR"
      }, { status: 422 }) : Response.json({ items: [webcam] })
    )));
    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await screen.findByText("Entrance");
    await user.click(screen.getByRole("button", { name: "Chỉnh sửa" }));
    await user.click(screen.getByRole("button", { name: "Lưu thay đổi (bản nháp)" }));
    await screen.findByText("Máy chủ từ chối cấu hình camera. Hãy kiểm tra nguồn camera và tải lại trang.");
    expect(screen.getByLabelText("Nguồn camera")).toBeTruthy();
  });
  it("allows browser webcam instead of requiring an RTSP URL", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return init?.method === "POST"
        ? Response.json({ ...camera, source_type: "webcam", rtsp_url: null }, { status: 201 })
        : Response.json({ items: [] });
    }));
    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await user.type(screen.getByLabelText("Tên camera"), "Browser webcam");
    await user.selectOptions(screen.getByLabelText("Nguồn"), "webcam");
    expect(screen.queryByLabelText("Địa chỉ RTSP")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Lưu camera" }));
    await waitFor(() => {
      const request = requests.find(item => item.init?.method === "POST");
      const body = JSON.parse(String(request?.init?.body));
      expect(body.source_type).toBe("webcam");
      expect(body.rtsp_url).toBeUndefined();
    });
  });
  it("creates an RTSP camera through the API and clears the credential", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let listCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === "/api/v1/cameras" && init?.method === "POST") {
        return Response.json(camera, { status: 201 });
      }
      listCount += 1;
      return Response.json({ items: listCount > 1 ? [camera] : [] });
    }));

    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await waitFor(() => expect(requests[0]?.url).toBe("/api/v1/cameras"));
    await user.type(screen.getByLabelText("Tên camera"), "Entrance");
    const secret = "rtsp://alice:supersecret@camera.local/live";
    await user.type(screen.getByLabelText("Địa chỉ RTSP"), secret);
    await user.click(screen.getByRole("button", { name: "Lưu camera" }));

    await screen.findByText("rtsp://***:***@camera.local/live");
    const create = requests.find((request) => request.init?.method === "POST");
    expect(JSON.parse(String(create?.init?.body))).toMatchObject({
      name: "Entrance",
      rtsp_url: secret,
      codec: "AUTO",
      enabled: true
    });
    expect(screen.queryByDisplayValue(secret)).toBeNull();
  });

  it("edits metadata without sending an empty replacement credential", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return init?.method === "PATCH"
        ? Response.json({ ...camera, name: "Lobby" })
        : Response.json({ items: [camera] });
    }));

    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await screen.findByText("Entrance");
    await user.click(screen.getByRole("button", { name: "Chỉnh sửa" }));
    const name = screen.getByLabelText("Tên");
    await user.clear(name);
    await user.type(name, "Lobby");
    await user.click(screen.getByRole("button", { name: "Lưu thay đổi (bản nháp)" }));

    await waitFor(() => {
      const patch = requests.find((request) => request.init?.method === "PATCH");
      const body = JSON.parse(String(patch?.init?.body));
      expect(body).toMatchObject({ name: "Lobby", codec: "AUTO" });
      expect(body).not.toHaveProperty("rtsp_url");
    });
  });

  it("opens the ROI editor directly from the camera card", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [camera] })));

    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await screen.findByText("Entrance");
    await user.click(screen.getByRole("button", { name: "Thiết lập vùng ROI" }));

    expect(screen.getByRole("heading", { name: "Vùng giám sát (ROI)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dừng vẽ" })).toBeTruthy();
    expect(screen.getByText(/ROI đang áp dụng: toàn khung hình/)).toBeTruthy();
  });

  it("keeps a browser webcam session mounted while editing its ROI", async () => {
    const browserCamera = { ...camera, source_type: "webcam" as const, rtsp_url: null, lifecycle: "ACTIVE" as const };
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack, addEventListener: vi.fn() }] })
    } });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/webcam/session") && init?.method === "POST") {
        return Response.json({ session_id: "session-1", token: "test-token", expires_in_seconds: 10 });
      }
      if (url.endsWith("/webcam/session") && init?.method === "DELETE") return new Response(null, { status: 204 });
      return Response.json({ items: [browserCamera] });
    }));

    const user = userEvent.setup();
    render(<CameraPanel runtimeStatuses={[]} />);
    await user.click(await screen.findByRole("button", { name: "Thiết lập vùng ROI" }));
    await user.click(await screen.findByRole("button", { name: "Bắt đầu realtime" }));
    await screen.findByRole("button", { name: "Dừng realtime" });
    await user.click(screen.getByRole("button", { name: "Dừng vẽ" }));

    expect(screen.getByRole("button", { name: "Dừng realtime" })).toBeTruthy();
    expect(stopTrack).not.toHaveBeenCalled();
  });
});
