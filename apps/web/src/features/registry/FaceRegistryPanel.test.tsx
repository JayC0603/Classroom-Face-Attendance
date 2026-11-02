import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import * as api from "../../api/registry";
import * as classApi from "../../api/classes";
import { FaceRegistryPanel } from "./FaceRegistryPanel";

vi.mock("../../api/classes", async importOriginal => ({
  ...await importOriginal<typeof import("../../api/classes")>(),
  listAdminPendingFaceReviews: vi.fn(), reviewAdminFaceImage: vi.fn()
}));

vi.mock("../../api/registry", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/registry")>(),
  listPersons: vi.fn(), createPerson: vi.fn(), listFaceImages: vi.fn(), uploadFaceImages: vi.fn()
}));

const person = {
  id: "person-1", full_name: "Nguyen An", student_id: "SV001", status: "ACTIVE" as const,
  person_number: 1, image_count: 0, recognition_ready: false as const, created_at: "2026-10-19T00:00:00Z",
  updated_at: "2026-10-19T00:00:00Z"
};
const image = {
  id: "image-1", person_id: person.id, original_filename: "face.png",
  mime_type: "image/jpeg" as const, width: 48, height: 48,
  processing_state: "PENDING_PROCESSING" as const, source: "MANUAL_UPLOAD" as const,
  created_at: "2026-10-19T00:00:00Z", content_url: "/api/v1/persons/person-1/face-images/image-1/content"
};

beforeEach(() => {
  vi.mocked(classApi.listAdminPendingFaceReviews).mockResolvedValue([]);
  vi.mocked(api.listPersons).mockResolvedValue({ items: [], total: 0, persistence: "postgresql" });
  vi.mocked(api.listFaceImages).mockResolvedValue([]);
  vi.mocked(api.createPerson).mockResolvedValue(person);
  vi.mocked(api.uploadFaceImages).mockResolvedValue([
    { filename: "face.png", status: "STORED_PENDING", image }
  ]);
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("creates a person, uploads selected files and shows pending gallery without readiness claims", async () => {
  const user = userEvent.setup();
  render(<FaceRegistryPanel />);
  await user.type(screen.getByLabelText("Họ và tên"), "Nguyen An");
  await user.type(screen.getByLabelText("Mã sinh viên"), "SV001");
  const file = new File(["sample"], "face.png", { type: "image/png" });
  await user.upload(screen.getByLabelText("Chọn ảnh khuôn mặt"), file);
  expect(screen.getByAltText("Xem trước face.png")).toBeTruthy();
  vi.mocked(api.listFaceImages).mockResolvedValue([image]);
  await user.click(screen.getByRole("button", { name: "Lưu hồ sơ và tải ảnh" }));
  await waitFor(() => expect(api.uploadFaceImages).toHaveBeenCalledWith(person.id, [file], "MANUAL_UPLOAD"));
  expect(api.createPerson).toHaveBeenCalledTimes(1);
  await screen.findByAltText("Ảnh của Nguyen An: face.png");
  expect(screen.getAllByText(/Chờ admin duyệt/).length).toBeGreaterThan(0);
  expect(screen.getByText(/t\u1ea3i l\u00ean s\u1ebd ch\u1edd duy\u1ec7t/)).toBeTruthy();
});

it("retries an upload failure using the existing person without recreating it", async () => {
  vi.mocked(api.uploadFaceImages).mockRejectedValueOnce(new Error("Mất kết nối"));
  const user = userEvent.setup();
  render(<FaceRegistryPanel />);
  await user.type(screen.getByLabelText("Họ và tên"), "Nguyen An");
  await user.type(screen.getByLabelText("Mã sinh viên"), "SV001");
  await user.upload(screen.getByLabelText("Chọn ảnh khuôn mặt"),
    new File(["sample"], "face.png", { type: "image/png" }));
  await user.click(screen.getByRole("button", { name: "Lưu hồ sơ và tải ảnh" }));
  await screen.findByText("Mất kết nối");
  await user.click(screen.getByRole("button", { name: "Tải ảnh lên" }));
  await waitFor(() => expect(api.uploadFaceImages).toHaveBeenCalledTimes(2));
  expect(api.createPerson).toHaveBeenCalledTimes(1);
});

it("reports per-file rejection and keeps only rejected files for retry", async () => {
  vi.mocked(api.uploadFaceImages).mockResolvedValueOnce([
    { filename: "face.png", status: "STORED_PENDING", image },
    { filename: "bad.png", status: "REJECTED", error_category: "INVALID_IMAGE" }
  ]);
  const user = userEvent.setup();
  render(<FaceRegistryPanel />);
  await user.type(screen.getByLabelText("Họ và tên"), "Nguyen An");
  await user.type(screen.getByLabelText("Mã sinh viên"), "SV001");
  await user.upload(screen.getByLabelText("Chọn ảnh khuôn mặt"), [
    new File(["sample"], "face.png", { type: "image/png" }),
    new File(["bad"], "bad.png", { type: "image/png" })
  ]);
  await user.click(screen.getByRole("button", { name: "Lưu hồ sơ và tải ảnh" }));
  await screen.findByText(/bad.png: Ảnh không hợp lệ/);
  expect(screen.queryByAltText("Xem trước face.png")).toBeNull();
  expect(screen.getByAltText("Xem trước bad.png")).toBeTruthy();
});

it("shows duplicate student ID and preserves draft inputs", async () => {
  vi.mocked(api.createPerson).mockRejectedValue(new Error("Mã sinh viên đã tồn tại"));
  const user = userEvent.setup();
  render(<FaceRegistryPanel />);
  await user.type(screen.getByLabelText("Họ và tên"), "Nguyen An");
  await user.type(screen.getByLabelText("Mã sinh viên"), "SV001");
  await user.click(screen.getByRole("button", { name: "Lưu hồ sơ và tải ảnh" }));
  await screen.findByText("Mã sinh viên đã tồn tại");
  expect(screen.getByDisplayValue("SV001")).toBeTruthy();
  expect(api.uploadFaceImages).not.toHaveBeenCalled();
});
