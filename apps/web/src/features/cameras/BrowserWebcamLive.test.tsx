import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import * as api from "../../api/browserCamera";
import { BrowserWebcamLive } from "./BrowserWebcamLive";

vi.mock("../../api/browserCamera", () => ({
  startBrowserSession: vi.fn(), sendBrowserFrame: vi.fn(), stopBrowserSession: vi.fn()
}));
const stop = vi.fn();
const getUserMedia = vi.fn();
const enumerateDevices = vi.fn();
const camera = { id: "camera", name: "Webcam", source_type: "webcam" as const,
  rtsp_url: null, codec: "AUTO" as const, enabled: true, lifecycle: "ACTIVE" as const,
  sampling_fps: 5, revision: "r1" };
const stream = { getTracks: () => [{ stop, addEventListener: vi.fn() }] };

beforeEach(() => {
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia, enumerateDevices } });
  enumerateDevices.mockResolvedValue([
    { kind: "videoinput", deviceId: "phone-1", label: "DroidCam 1" },
    { kind: "videoinput", deviceId: "phone-2", label: "DroidCam 2" },
    { kind: "audioinput", deviceId: "mic", label: "Microphone" }
  ]);
  getUserMedia.mockResolvedValue(stream);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.mocked(api.startBrowserSession).mockResolvedValue({ session_id: "s1", token: "secret", expires_in_seconds: 10 });
  vi.mocked(api.sendBrowserFrame).mockResolvedValue();
  vi.mocked(api.stopBrowserSession).mockResolvedValue();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(cb => cb(new Blob(["jpeg"], { type: "image/jpeg" })));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });

it("streams bounded frames and stops the camera/session when requested", async () => {
  const user = userEvent.setup();
  render(<BrowserWebcamLive camera={camera} />);
  expect(getUserMedia).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Bắt đầu realtime" }));
  const video = screen.getByLabelText("Webcam realtime Webcam") as HTMLVideoElement;
  Object.defineProperty(video, "videoWidth", { value: 640, configurable: true });
  Object.defineProperty(video, "videoHeight", { value: 480, configurable: true });
  fireEvent.loadedData(video);
  await waitFor(() => expect(api.sendBrowserFrame).toHaveBeenCalledTimes(1));
  expect(api.sendBrowserFrame).toHaveBeenCalledWith(camera.id, "secret", expect.any(Blob), expect.any(AbortSignal));
  await user.click(screen.getByRole("button", { name: "Dừng realtime" }));
  expect(stop).toHaveBeenCalledTimes(1);
  expect(api.stopBrowserSession).toHaveBeenCalledWith(camera.id, "secret");
});

it("does not queue frames while an upload is pending, and releases on unmount", async () => {
  let resolve!: () => void;
  vi.mocked(api.sendBrowserFrame).mockReturnValue(new Promise<void>(r => { resolve = r; }));
  const user = userEvent.setup();
  const view = render(<BrowserWebcamLive camera={camera} />);
  const video = screen.getByLabelText("Webcam realtime Webcam") as HTMLVideoElement;
  Object.defineProperty(video, "videoWidth", { value: 640, configurable: true });
  Object.defineProperty(video, "videoHeight", { value: 480, configurable: true });
  await user.click(screen.getByRole("button", { name: "Bắt đầu realtime" }));
  await waitFor(() => expect(api.sendBrowserFrame).toHaveBeenCalledTimes(1));
  await act(async () => { await new Promise(r => setTimeout(r, 300)); });
  expect(api.sendBrowserFrame).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => { resolve(); });
  expect(stop).toHaveBeenCalledTimes(1);
  expect(api.stopBrowserSession).toHaveBeenCalledTimes(1);
});

it("releases a camera permission grant arriving after unmount", async () => {
  let resolve!: (value: unknown) => void;
  getUserMedia.mockReturnValue(new Promise(r => { resolve = r; }));
  const user = userEvent.setup();
  const view = render(<BrowserWebcamLive camera={camera} />);
  await user.click(screen.getByRole("button", { name: "Bắt đầu realtime" }));
  view.unmount();
  await act(async () => { resolve(stream); });
  expect(stop).toHaveBeenCalledTimes(1);
  expect(api.startBrowserSession).not.toHaveBeenCalled();
});

it("opens the exact selected DroidCam and locks selection while streaming", async () => {
  const user = userEvent.setup();
  render(<BrowserWebcamLive camera={camera} />);
  await screen.findByRole("option", { name: "DroidCam 2" });
  expect(screen.queryByRole("option", { name: "Microphone" })).toBeNull();
  await user.selectOptions(screen.getByRole("combobox"), "phone-2");
  await user.click(screen.getByRole("button", { name: "Bắt đầu realtime" }));
  expect(getUserMedia).toHaveBeenCalledWith({
    video: { deviceId: { exact: "phone-2" }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 15 } }, audio: false
  });
  expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Dừng realtime" }));
  await waitFor(() => expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(false));
});

it("keeps device choices independent for different cameras", async () => {
  const user = userEvent.setup();
  render(<><BrowserWebcamLive camera={camera} />
    <BrowserWebcamLive camera={{ ...camera, id: "other", name: "Other" }} /></>);
  await waitFor(() => expect(screen.getAllByRole("option", { name: "DroidCam 2" })).toHaveLength(2));
  const selects = screen.getAllByRole("combobox");
  await user.selectOptions(selects[0], "phone-1");
  await user.selectOptions(selects[1], "phone-2");
  expect((selects[0] as HTMLSelectElement).value).toBe("phone-1");
  expect((selects[1] as HTMLSelectElement).value).toBe("phone-2");
});

it("releases temporary permission streams even after unmount", async () => {
  const user = userEvent.setup();
  let resolve!: (value: unknown) => void;
  getUserMedia.mockReturnValue(new Promise(r => { resolve = r; }));
  const view = render(<BrowserWebcamLive camera={camera} />);
  await screen.findByRole("option", { name: "DroidCam 1" });
  await user.click(screen.getByRole("button", { name: "Cấp quyền / làm mới webcam" }));
  view.unmount();
  await act(async () => { resolve(stream); });
  expect(stop).toHaveBeenCalledTimes(1);
  expect(api.startBrowserSession).not.toHaveBeenCalled();
});

it("reports an unavailable selected device without falling back to another webcam", async () => {
  const user = userEvent.setup();
  getUserMedia.mockRejectedValue(new DOMException("Device missing", "OverconstrainedError"));
  render(<BrowserWebcamLive camera={camera} />);
  await screen.findByRole("option", { name: "DroidCam 2" });
  await user.selectOptions(screen.getByRole("combobox"), "phone-2");
  await user.click(screen.getByRole("button", { name: "Bắt đầu realtime" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Không tìm thấy webcam đã chọn");
  expect(getUserMedia).toHaveBeenCalledTimes(1);
  expect(api.startBrowserSession).not.toHaveBeenCalled();
});
