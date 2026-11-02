import pytest
from onnx import TensorProto, helper


def graph(height=608, width=1088):
    model = helper.make_model(
        helper.make_graph(
            [
                helper.make_node(
                    "Constant",
                    [],
                    ["output"],
                    value=helper.make_tensor(
                        "v", TensorProto.FLOAT, [1, 13566, 6], [0.0] * (13566 * 6)
                    ),
                )
            ],
            "controlled-yolox-contract",
            [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, height, width])],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 13566, 6])],
        ),
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def test_person_model_edit_and_delete_respect_deployment_lifecycle(tmp_path):
    from auth_test_support import TestClient, create_app
    from control_api.model_artifacts import (
        LocalModelArtifactStorage,
        ModelArtifactIngestor,
        OnnxArtifactInspector,
    )
    from control_api.model_registry import InMemoryModelRepository, ModelRegistryService

    registry = ModelRegistryService(
        InMemoryModelRepository(),
        ModelArtifactIngestor(
            LocalModelArtifactStorage(tmp_path), OnnxArtifactInspector(), 1024 * 1024
        ),
    )
    api = TestClient(create_app(model_registry_service=registry))
    created = api.post(
        "/api/v1/person-models",
        data={"version": "v1", "decode_mode": "yolox_raw"},
        files={"file": ("person.onnx", graph(), "application/octet-stream")},
    )
    assert created.status_code == 201
    version_id = created.json()["id"]
    path = f"/api/v1/person-models/{version_id}"
    changed = api.patch(path, json={"version": "renamed", "confidence": 0.6})
    assert changed.status_code == 200
    assert changed.json()["confidence"] == 0.6
    assert changed.json()["version"] == "renamed"
    assert api.patch(path, json={"version": " ", "confidence": 0.3}).status_code == 409
    deployment = api.post(
        f"/api/v1/model-versions/{version_id}/deployments",
        json={"backend": "ONNX_CPU", "precision": "FP32"},
    ).json()
    assert api.patch(path, json={"version": "renamed", "confidence": 0.4}).status_code == 409
    assert api.patch(path, json={"version": "display-name"}).status_code == 200
    api.post(f"/api/v1/deployments/{deployment['id']}/activate")
    assert api.delete(path).status_code == 409
    api.post(f"/api/v1/deployments/{deployment['id']}/deactivate")
    # Even a stale, inactive assignment protects the selected model.
    from fr_domain.model_management import ModelType

    registry.repository.put_assignment(ModelType.PERSON_DETECTOR, deployment["id"])
    assert api.delete(path).status_code == 409
    registry.repository.assignments.clear()
    assert api.delete(path).status_code == 204
    assert api.get("/api/v1/person-models").json()["items"] == []
    assert registry.list_deployments() == []
    assert api.delete(path).status_code == 404


def test_upload_manifest_reads_names_and_non_square_dimensions_from_graph():
    from control_api.person_models import inspect_yolox_manifest

    manifest = inspect_yolox_manifest(graph(), "yolox_raw", 0, 0.3)
    assert manifest.inputs[0].name == "input"
    assert manifest.inputs[0].shape == [1, 3, 608, 1088]
    assert manifest.outputs[0].name == "output"
    assert manifest.metadata["class_count"] == 1
    assert manifest.preprocessing.normalization == "raw_0_255"


def test_upload_rejects_ambiguous_or_incompatible_person_exports():
    from control_api.person_models import inspect_yolox_manifest

    with pytest.raises(ValueError):
        inspect_yolox_manifest(b"bad", "yolox_raw", 0, 0.3)
    with pytest.raises(ValueError):
        inspect_yolox_manifest(graph(640, 640), "yolox_raw", 0, 0.3)
    with pytest.raises(ValueError):
        inspect_yolox_manifest(graph(), "yolox_raw", 1, 0.3)


def test_upload_recognizes_xyxy_score_class_wrapper_and_rejects_wrong_mode():
    import onnx
    from control_api.person_models import inspect_yolox_manifest

    model = onnx.load_model_from_string(graph())
    model.graph.node[0].output[0] = "head"
    for name, value in [("s0", 0), ("s4", 4), ("s5", 5), ("s6", 6), ("axis", 2)]:
        model.graph.initializer.append(helper.make_tensor(name, TensorProto.INT64, [1], [value]))
    matrix = [1, 0, 1, 0, 0, 1, 0, 1, -0.5, 0, 0.5, 0, 0, -0.5, 0, 0.5]
    model.graph.initializer.append(helper.make_tensor("matrix", TensorProto.FLOAT, [4, 4], matrix))
    model.graph.node.extend(
        [
            helper.make_node("Slice", ["head", "s0", "s4", "axis"], ["xywh"]),
            helper.make_node("Slice", ["head", "s4", "s5", "axis"], ["objectness"]),
            helper.make_node("Slice", ["head", "s5", "s6", "axis"], ["classes"]),
            helper.make_node("MatMul", ["xywh", "matrix"], ["xyxy"]),
            helper.make_node("ReduceMax", ["classes"], ["best_class"], axes=[-1], keepdims=1),
            helper.make_node("ArgMax", ["classes"], ["label"], axis=-1, keepdims=1),
            helper.make_node("Mul", ["best_class", "objectness"], ["score"]),
            helper.make_node("Cast", ["label"], ["class_id"], to=TensorProto.FLOAT),
            helper.make_node("Concat", ["xyxy", "score", "class_id"], ["output"], axis=-1),
        ]
    )
    payload = model.SerializeToString()
    manifest = inspect_yolox_manifest(payload, "auto", 0, 0.5)
    assert manifest.metadata["output_semantics"] == "yolox_xyxy"
    assert "class_count" not in manifest.metadata
    assert inspect_yolox_manifest(payload, "auto", 2, 0.5).metadata["person_class_id"] == 2
    with pytest.raises(ValueError, match="XYXY_SCORE_CLASS"):
        inspect_yolox_manifest(payload, "yolox_raw", 0, 0.5)
    with pytest.raises(ValueError, match="OUTPUT_MODE_REQUIRED"):
        inspect_yolox_manifest(graph(), "auto", 0, 0.5)


def test_person_upload_is_draft_and_does_not_activate_without_worker_validation(tmp_path):
    from auth_test_support import TestClient, create_app
    from control_api.model_artifacts import (
        LocalModelArtifactStorage,
        ModelArtifactIngestor,
        OnnxArtifactInspector,
    )
    from control_api.model_registry import InMemoryModelRepository, ModelRegistryService

    registry = ModelRegistryService(
        InMemoryModelRepository(),
        ModelArtifactIngestor(
            LocalModelArtifactStorage(tmp_path), OnnxArtifactInspector(), 1024 * 1024
        ),
    )
    client = TestClient(create_app(model_registry_service=registry, internal_camera_token="worker"))
    assert (
        client.post("/api/v1/person-models", content=b"x" * (1024 * 1024 + 65537)).status_code
        == 413
    )

    def oversized_chunks():
        yield (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; '
            b'filename="yolox.onnx"\r\n\r\n'
        )
        for _ in range(20):
            yield b"x" * 65536
        yield b"\r\n--boundary--\r\n"

    assert (
        client.post(
            "/api/v1/person-models",
            content=oversized_chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        ).status_code
        == 413
    )
    assert registry.list_definitions() == []
    response = client.post(
        "/api/v1/person-models",
        data={"version": "v1", "decode_mode": "yolox_raw", "person_class_id": 0, "confidence": 0.3},
        files={"file": ("yolox.onnx", graph(), "application/octet-stream")},
    )
    assert response.status_code == 201
    page = client.get("/api/v1/person-models").json()
    assert page["active_deployment_id"] is None
    assert len(page["items"]) == 1
    assert page["items"][0]["input_shape"] == [1, 3, 608, 1088]
    assert page["items"][0]["deployments"] == []
    assert "storage_key" not in response.text
    # Controlled contract graph, not trained weights: exercise the real CPU adapter
    # and existing activation/assignment lifecycle without installing it in production.
    from fastapi.encoders import jsonable_encoder
    from fr_domain.model_management import DeploymentBackend
    from vision_worker.model_delivery import PendingModelDeployment, ProductionAdapterFactory
    from vision_worker.model_runtime import ResolvedModelRuntime

    deployment = client.post(
        f"/api/v1/model-versions/{response.json()['id']}/deployments",
        json={"backend": "ONNX_CPU", "precision": "FP32"},
    ).json()
    deployment_id = deployment["id"]
    assert client.post(f"/api/v1/deployments/{deployment_id}/activate").status_code == 200
    assignment = {"model_type": "PERSON_DETECTOR", "deployment_id": deployment_id}
    assert client.put("/api/v1/model-assignments", json=assignment).status_code == 409
    internal = jsonable_encoder(registry.internal_deployment(deployment_id))
    artifact_response = client.get(
        internal["artifact"]["content_url"], headers={"X-Worker-Token": "worker"}
    )
    assert artifact_response.status_code == 200
    downloaded = tmp_path / "worker-cache.onnx"
    downloaded.write_bytes(artifact_response.content)
    pending = PendingModelDeployment.from_api(internal, artifact_path=downloaded)
    resolved = ResolvedModelRuntime(
        DeploymentBackend.ONNX_CPU, DeploymentBackend.ONNX_CPU, "CPUExecutionProvider"
    )
    adapter = ProductionAdapterFactory().prepare(pending, resolved)
    try:
        adapter.warmup()
        adapter.health_check()
        assert (
            client.post(
                f"/api/v1/internal/model-deployments/{deployment_id}/activation-result",
                headers={"X-Worker-Token": "worker"},
                json={
                    "success": True,
                    "resolved_backend": "ONNX_CPU",
                    "execution_provider": "CPUExecutionProvider",
                },
            ).status_code
            == 200
        )
        assert client.put("/api/v1/model-assignments", json=assignment).status_code == 200
        page = client.get("/api/v1/person-models").json()
        assert page["active_deployment_id"] == deployment_id
        assert page["items"][0]["deployments"][0]["runtime_state"] == "ACTIVE"
    finally:
        adapter.close()


def test_person_upload_is_idempotent_by_artifact_checksum(tmp_path):
    from auth_test_support import TestClient, create_app
    from control_api.model_artifacts import (
        LocalModelArtifactStorage,
        ModelArtifactIngestor,
        OnnxArtifactInspector,
    )
    from control_api.model_registry import InMemoryModelRepository, ModelRegistryService

    registry = ModelRegistryService(
        InMemoryModelRepository(),
        ModelArtifactIngestor(
            LocalModelArtifactStorage(tmp_path), OnnxArtifactInspector(), 1024 * 1024
        ),
    )
    client = TestClient(create_app(model_registry_service=registry))
    payload = graph()

    first = client.post(
        "/api/v1/person-models",
        data={"version": "trained-v1", "decode_mode": "yolox_raw"},
        files={"file": ("person.onnx", payload, "application/octet-stream")},
    )
    second = client.post(
        "/api/v1/person-models",
        data={"version": "imported-copy", "decode_mode": "yolox_raw"},
        files={"file": ("person-copy.onnx", payload, "application/octet-stream")},
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(registry.list_definitions()) == 1
    assert len(registry.list_versions(registry.list_definitions()[0].id)) == 1
