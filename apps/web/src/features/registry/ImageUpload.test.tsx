import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { ImageUpload } from "./ImageUpload";

vi.mock("./WebcamCapture", () => ({
  WebcamCapture: ({ onCapture }: { onCapture(file: File): void }) => <button type="button"
    onClick={() => onCapture(new File(["jpeg"], "webcam-test.jpg", { type: "image/jpeg" }))}>
    Chụp ảnh thử
  </button>
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("sends captured images with camera provenance and clears drafts when changing sources", async () => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
  const onUpload = vi.fn().mockResolvedValue([
    { filename: "webcam-test.jpg", status: "STORED_PENDING" }
  ]);
  const user = userEvent.setup();
  render(<ImageUpload busy={false} resetKey={0} isExisting onUpload={onUpload} />);
  await user.click(screen.getByRole("radio", { name: "Dùng webcam" }));
  expect(screen.queryByLabelText("Chọn ảnh khuôn mặt")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Chụp ảnh thử" }));
  await screen.findByAltText("Xem trước webcam-test.jpg");
  await user.click(screen.getByRole("button", { name: "Tải ảnh lên" }));
  await waitFor(() => expect(onUpload).toHaveBeenCalledWith(
    [expect.objectContaining({ name: "webcam-test.jpg", type: "image/jpeg" })], "CAMERA_CAPTURE"
  ));
  await user.click(screen.getByRole("button", { name: "Chụp ảnh thử" }));
  await screen.findByAltText("Xem trước webcam-test.jpg");
  await user.click(screen.getByRole("radio", { name: "Tải ảnh từ máy" }));
  expect(screen.queryByAltText("Xem trước webcam-test.jpg")).toBeNull();
  expect(screen.getByLabelText("Chọn ảnh khuôn mặt")).toBeTruthy();
});
