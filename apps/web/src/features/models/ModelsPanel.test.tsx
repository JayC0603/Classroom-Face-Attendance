import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdvancedModelsPanel as ModelsPanel } from "./AdvancedModelsPanel";
import type { ModelDefinitionPublic } from "../../api/contracts";

const yolo: ModelDefinitionPublic = {
  id: "model-1",
  name: "YOLO Model",
  model_type: "PERSON_DETECTOR",
  framework: "ONNX",
  description: "",
  versions: [],
  deployments: []
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ModelsPanel", () => {
  it("shows truthful first-run readiness and creates a role-specific model", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let created = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === "/api/v1/models" && init?.method === "POST") {
        created = true;
        return Response.json(yolo, { status: 201 });
      }
      if (url === "/api/v1/models") {
        return Response.json({ items: created ? [yolo] : [] });
      }
      if (url === "/api/v1/recognition/readiness") {
        return Response.json({
          status: "NOT_READY",
          reasons: ["PERSON_DETECTOR_NOT_ASSIGNED"],
          assignments: {},
          vector_index_status: "NOT_CONFIGURED"
        });
      }
      return Response.json({}, { status: 404 });
    }));

    const user = userEvent.setup();
    render(<ModelsPanel />);
    expect(await screen.findByText("Pipeline NOT_READY")).toBeTruthy();
    expect(screen.getAllByText("Chưa cấu hình")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "Thêm model YOLO" }));
    expect(await screen.findByText("YOLO Model")).toBeTruthy();
    const create = requests.find((request) => request.init?.method === "POST");
    expect(JSON.parse(String(create?.init?.body))).toMatchObject({
      model_type: "PERSON_DETECTOR",
      framework: "ONNX"
    });
    await waitFor(() => expect(screen.getAllByText("Chưa cấu hình")).toHaveLength(2));
  });
});
