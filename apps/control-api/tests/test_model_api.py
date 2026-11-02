from pathlib import Path

from auth_test_support import TestClient, create_app
from control_api.model_artifacts import LocalModelArtifactStorage, ModelArtifactIngestor
from control_api.model_registry import InMemoryModelRepository, ModelRegistryService
from test_model_artifacts import Inspector
from test_model_domain import person_manifest


def test_model_upload_deploy_and_worker_activation_flow(tmp_path: Path) -> None:
    service = ModelRegistryService(
        InMemoryModelRepository(),
        ModelArtifactIngestor(LocalModelArtifactStorage(tmp_path), Inspector(), 1024),
    )
    api = TestClient(
        create_app(model_registry_service=service, internal_camera_token="worker-token")
    )
    definition = api.post(
        "/api/v1/models",
        json={"name": "YOLO Person", "model_type": "PERSON_DETECTOR", "framework": "ONNX"},
    )
    assert definition.status_code == 201
    definition_id = definition.json()["id"]
    uploaded = api.post(
        f"/api/v1/models/{definition_id}/versions",
        data={
            "version": "v1",
            "manifest": person_manifest().model_dump_json(),
        },
        files={"file": ("../yolo.onnx", b"controlled-onnx-v1", "application/octet-stream")},
    )
    assert uploaded.status_code == 201
    person_models = api.get("/api/v1/person-models")
    assert person_models.status_code == 200
    assert person_models.json()["items"][0]["architecture"] == "YOLO"
    assert person_models.json()["items"][0]["confidence"] == 0.25
    assert "storage_key" not in uploaded.text
    assert uploaded.json()["checksum"] == (
        "2d2e33652bfc898454b8b5a63124084e0448c514bafd2bb5213e097f552edd78"
    )
    deployment = api.post(
        f"/api/v1/model-versions/{uploaded.json()['id']}/deployments",
        json={"backend": "AUTO", "precision": "FP32"},
    ).json()
    pending = api.post(f"/api/v1/deployments/{deployment['id']}/activate")
    assert pending.json()["status"] == "ACTIVATION_PENDING"
    assert api.get("/api/v1/internal/model-deployments/pending").status_code == 403
    delivered = api.get(
        "/api/v1/internal/model-deployments/pending",
        headers={"X-Worker-Token": "worker-token"},
    )
    assert delivered.status_code == 200
    artifact = delivered.json()["items"][0]["artifact"]
    assert artifact["content_url"] == (f"/api/v1/internal/model-artifacts/{artifact['id']}/content")
    assert "storage_key" not in artifact
    content_url = artifact["content_url"]
    assert api.get(content_url).status_code == 403
    content = api.get(content_url, headers={"X-Worker-Token": "worker-token"})
    assert content.status_code == 200
    assert content.content == b"controlled-onnx-v1"
    result = api.post(
        f"/api/v1/internal/model-deployments/{deployment['id']}/activation-result",
        headers={"X-Worker-Token": "worker-token"},
        json={
            "success": True,
            "resolved_backend": "ONNX_CPU",
            "execution_provider": "CPUExecutionProvider",
            "fallback_reason": "CUDA_PROVIDER_UNAVAILABLE",
        },
    )
    assert result.json()["status"] == "ACTIVE"
    public = api.get("/api/v1/models").text
    assert "storage_key" not in public
    assert "controlled-onnx" not in public
    runtime = api.get(f"/api/v1/deployments/{deployment['id']}/runtime").json()
    assert runtime["state"] == "ACTIVE"
    assert runtime["execution_provider"] == "CPUExecutionProvider"
    assigned = api.put(
        "/api/v1/model-assignments",
        json={"model_type": "PERSON_DETECTOR", "deployment_id": deployment["id"]},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assignments"]["PERSON_DETECTOR"] == deployment["id"]
    assert api.get("/api/v1/recognition/readiness").json()["status"] == "NOT_READY"
    assert api.get("/api/v1/internal/model-deployments/assigned").status_code == 403
    selected = api.get(
        "/api/v1/internal/model-deployments/assigned",
        headers={"X-Worker-Token": "worker-token"},
    ).json()["items"]
    assert len(selected) == 1
    assert selected[0]["deployment"]["id"] == deployment["id"]
    assert selected[0]["version"]["manifest"]["model_type"] == "PERSON_DETECTOR"
