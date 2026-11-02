import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { WebcamCapture } from "./WebcamCapture";

const stop = vi.fn();
const getUserMedia = vi.fn();
const stream = { getTracks: () => [{ stop, addEventListener: vi.fn(), removeEventListener: vi.fn() }] };

beforeEach(() => {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true, value: { getUserMedia }
  });
  getUserMedia.mockResolvedValue(stream);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });

it("opens webcam only on request, captures a JPEG and releases camera on stop", async () => {
  const onCapture = vi.fn();
  const user = userEvent.setup();
  render(<WebcamCapture disabled={false} remaining={10} onCapture={onCapture} />);
  expect(getUserMedia).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Bật webcam" }));
  await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: false })));
  const video = screen.getByLabelText("Hình ảnh webcam") as HTMLVideoElement;
  Object.defineProperty(video, "videoWidth", { configurable: true, value: 640 });
  Object.defineProperty(video, "videoHeight", { configurable: true, value: 480 });
  fireEvent.loadedData(video);
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback => {
    callback(new Blob(["jpeg"], { type: "image/jpeg" }));
  });
  await user.click(screen.getByRole("button", { name: "Chụp ảnh" }));
  await waitFor(() => expect(onCapture).toHaveBeenCalledTimes(1));
  const file = onCapture.mock.calls[0][0] as File;
  expect(file.type).toBe("image/jpeg");
  expect(file.name).toMatch(/^webcam-.*\.jpg$/);
  expect(drawImage).toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Tắt webcam" }));
  expect(stop).toHaveBeenCalledTimes(1);
});

it("shows permission errors with a retry action", async () => {
  getUserMedia.mockRejectedValue(new DOMException("Permission denied", "NotAllowedError"));
  const user = userEvent.setup();
  render(<WebcamCapture disabled={false} remaining={10} onCapture={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Bật webcam" }));
  await screen.findByText(/Chưa được cấp quyền truy cập webcam/);
  expect(screen.getByRole("button", { name: "Bật webcam" })).toBeTruthy();
});

it("releases a stream granted after the component has unmounted", async () => {
  let resolve!: (value: unknown) => void;
  getUserMedia.mockReturnValue(new Promise(value => { resolve = value; }));
  const user = userEvent.setup();
  const view = render(<WebcamCapture disabled={false} remaining={10} onCapture={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Bật webcam" }));
  view.unmount();
  await act(async () => { resolve(stream); });
  expect(stop).toHaveBeenCalledTimes(1);
});

it("stops an active webcam when saving starts", async () => {
  const user = userEvent.setup();
  const view = render(<WebcamCapture disabled={false} remaining={10} onCapture={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Bật webcam" }));
  await screen.findByRole("button", { name: "Tắt webcam" });
  view.rerender(<WebcamCapture disabled remaining={10} onCapture={vi.fn()} />);
  await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
});
