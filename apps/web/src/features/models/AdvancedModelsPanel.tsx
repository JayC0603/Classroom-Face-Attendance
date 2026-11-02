import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  activateModelDeployment,
  assignModel,
  createModel,
  createModelDeployment,
  getRecognitionReadiness,
  listModels,
  uploadModelVersion
} from "../../api/client";
import type {
  DeploymentBackend,
  ModelDefinitionPublic,
  ModelType,
  RecognitionReadiness
} from "../../api/contracts";

const roles: Array<{ type: ModelType; label: string; preset: string }> = [
  { type: "PERSON_DETECTOR", label: "Phát hiện người", preset: "YOLO" },
  { type: "FACE_DETECTOR", label: "Phát hiện khuôn mặt", preset: "SCRFD" },
  { type: "FACE_EMBEDDER", label: "Đặc trưng khuôn mặt", preset: "AdaFace" }
];

function manifestPreset(type: ModelType) {
  const faceEmbedder = type === "FACE_EMBEDDER";
  return {
    schema_version: 1,
    model_type: type,
    inputs: [{ name: "input", shape: [1, 3, faceEmbedder ? 112 : 640, faceEmbedder ? 112 : 640], dtype: "float32" }],
    outputs: [{ name: "output", shape: faceEmbedder ? [1, 512] : [1, "detections"], dtype: "float32" }],
    preprocessing: {
      layout: "NCHW",
      color_order: "RGB",
      normalization: faceEmbedder ? "adaface_default" : "zero_to_one",
      resize_mode: faceEmbedder ? "aligned" : "letterbox"
    },
    metadata: type === "PERSON_DETECTOR"
      ? { architecture: "YOLO", output_semantics: "detections", person_class_id: 0 }
      : type === "FACE_DETECTOR"
        ? { architecture: "SCRFD", output_semantics: "boxes_scores_landmarks", landmark_points: 5 }
        : {
            architecture: "AdaFace",
            embedding_dimension: 512,
            l2_normalized: true,
            distance_metric: "cosine",
            alignment_profile: "arcface_112x112"
          },
    runtime_compatibility: ["ONNX_CPU", "ONNX_CUDA"]
  };
}

export function AdvancedModelsPanel() {
  const [models, setModels] = useState<ModelDefinitionPublic[]>([]);
  const [readiness, setReadiness] = useState<RecognitionReadiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [nextModels, nextReadiness] = await Promise.all([
      listModels(),
      getRecognitionReadiness()
    ]);
    setModels(nextModels);
    setReadiness(nextReadiness);
  }, []);

  useEffect(() => { refresh().catch(() => setError("Unable to load model configuration")); }, [refresh]);

  async function addModel(role: (typeof roles)[number]) {
    setBusy(true); setError(null);
    try {
      await createModel({ name: `${role.preset} Model`, model_type: role.type, framework: "ONNX" });
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Model creation failed"); }
    finally { setBusy(false); }
  }

  async function upload(event: FormEvent<HTMLFormElement>, model: ModelDefinitionPublic) {
    event.preventDefault(); setBusy(true); setError(null);
    const data = new FormData(event.currentTarget);
    const file = data.get("file");
    try {
      if (!(file instanceof File) || file.size === 0) throw new Error("Select an ONNX file");
      await uploadModelVersion(model.id, String(data.get("version")), manifestPreset(model.model_type), file);
      await refresh();
      event.currentTarget.reset();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Upload failed"); }
    finally { setBusy(false); }
  }

  async function deploy(versionId: string, backend: DeploymentBackend) {
    setBusy(true); setError(null);
    try {
      const deployment = await createModelDeployment(versionId, backend);
      await activateModelDeployment(deployment.id);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Deployment failed"); }
    finally { setBusy(false); }
  }

  return (
      <section className="advanced-models" aria-labelledby="models-heading">
      <div className="section-heading">
        <div><p className="eyebrow">QUẢN LÝ MODEL</p><h2 id="models-heading">Cấu hình nâng cao</h2></div>
        <span className={`readiness ${readiness?.status === "READY" ? "ready" : "not-ready"}`}>
          Pipeline {readiness?.status ?? "CHECKING"}
        </span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="grid">
        {roles.map((role) => {
          const items = models.filter((item) => item.model_type === role.type);
          return (
            <article key={role.type}>
              <h3>{role.label}</h3>
              {items.length === 0 ? (
                <><p className="not-configured">Chưa cấu hình</p><button disabled={busy} onClick={() => addModel(role)}>Thêm model {role.preset}</button></>
              ) : items.map((model) => (
                <div key={model.id} className="model-card">
                  <strong>{model.name}</strong><span>{model.framework}</span>
                  {model.versions.map((version) => (
                    <div key={version.id} className="model-version">
                      <span>{version.version} · {version.format} · {version.checksum.slice(0, 12)}</span>
                      <span>{version.validation_status}</span>
                      <button disabled={busy} onClick={() => deploy(version.id, "AUTO")}>Triển khai tự động</button>
                    </div>
                  ))}
                  <form onSubmit={(event) => upload(event, model)}>
                    <label>Phiên bản<input name="version" required placeholder="v1" /></label>
                    <label>Tệp ONNX<input name="file" type="file" accept=".onnx,application/octet-stream" required /></label>
                    <button disabled={busy} type="submit">Kiểm tra và tải lên</button>
                  </form>
                  {model.deployments.map((deployment) => (
                    <p key={deployment.id}>{deployment.backend} · {deployment.status}{deployment.error_category ? ` · ${deployment.error_category}` : ""}
                      {deployment.status === "ACTIVE" && (
                        <button disabled={busy} onClick={async () => {
                          await assignModel(model.model_type, deployment.id); await refresh();
                        }}>Dùng cho nhận diện</button>
                      )}</p>
                  ))}
                </div>
              ))}
            </article>
          );
        })}
      </div>
      {readiness?.reasons.length ? <p>Not ready: {readiness.reasons.join(", ")}</p> : null}
    </section>
  );
}
