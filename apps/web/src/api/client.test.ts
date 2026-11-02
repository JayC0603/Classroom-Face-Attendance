import { afterEach, expect, it, vi } from "vitest";

import { connectRuntimeStatus, updateCamera } from "./client";

afterEach(() => vi.unstubAllGlobals());

it('reconnects runtime observation and cancels retries when unmounted', async () => {
  vi.useFakeTimers();
  class Socket extends EventTarget {
    static instances: Socket[] = [];
    close = vi.fn();
    constructor() {super(); Socket.instances.push(this);}
  }
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({items: []})));
  const receive = vi.fn();
  const stop = connectRuntimeStatus(receive);
  try {
    Socket.instances[0].dispatchEvent(new Event('open'));
    Socket.instances[0].dispatchEvent(new MessageEvent('message', {data: 'invalid json'}));
    expect(receive).not.toHaveBeenCalled();
    Socket.instances[0].dispatchEvent(new CloseEvent('close', {code: 1006}));
    await vi.advanceTimersByTimeAsync(1600);
    expect(Socket.instances.length).toBe(2);
    Socket.instances[1].dispatchEvent(new CloseEvent('close', {code: 1006}));
    stop();
    await vi.advanceTimersByTimeAsync(35000);
    expect(Socket.instances.length).toBe(2);
    expect(Socket.instances[1].close).toHaveBeenCalledOnce();
  } finally {stop(); vi.useRealTimers();}
});

it("shows actionable camera validation errors without exposing request credentials", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({
    detail: "request validation failed", error_category: "VALIDATION_ERROR"
  }, { status: 422 })));
  await expect(updateCamera("camera", { source_type: "webcam" })).rejects.toThrow(
    "Máy chủ từ chối cấu hình camera. Hãy kiểm tra nguồn camera và tải lại trang."
  );
});

it("does not display arbitrary server error details", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({
    detail: "private credential or database path"
  }, { status: 500 })));
  await expect(updateCamera("camera", {})).rejects.toThrow("Máy chủ gặp lỗi (500). Vui lòng thử lại.");
});
