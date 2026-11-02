import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _client_for(activation_state: str, calls: list[str]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/api/v1/person-models" and request.method == "POST":
            assert b"known model" in request.read()
            return httpx.Response(201, json={"id": "version-1"})
        if path == "/api/v1/person-models":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "version-1",
                            "deployments": [{"id": "deployment-1", "status": "DRAFT"}],
                        }
                    ]
                },
            )
        if path == "/api/v1/model-assignments" and request.method == "GET":
            return httpx.Response(200, json={"assignments": {}})
        if path == "/api/v1/model-versions/version-1/deployments":
            assert json.loads(request.read()) == {"backend": "ONNX_CPU", "precision": "FP32"}
            return httpx.Response(201, json={"id": "deployment-1", "status": "DRAFT"})
        if path == "/api/v1/deployments/deployment-1/activate":
            return httpx.Response(200, json={"status": "ACTIVATION_PENDING"})
        if path == "/api/v1/deployments/deployment-1/runtime":
            return httpx.Response(
                200,
                json={
                    "configured_status": activation_state,
                    "state": activation_state,
                    "error_category": "BAD_ONNX" if activation_state == "ERROR" else None,
                },
            )
        if path == "/api/v1/model-assignments" and request.method == "PUT":
            assert json.loads(request.read()) == {
                "model_type": "PERSON_DETECTOR",
                "deployment_id": "deployment-1",
            }
            return httpx.Response(200, json={"assignments": {"PERSON_DETECTOR": "deployment-1"}})
        return httpx.Response(404)

    return httpx.Client(
        base_url="http://control-api.local",
        transport=httpx.MockTransport(respond),
    )


def test_import_assigns_only_after_worker_reports_active(tmp_path: Path) -> None:
    from scripts.import_person_model import import_person_model

    model = tmp_path / "person.onnx"
    model.write_bytes(b"known model")
    calls: list[str] = []
    with _client_for("ACTIVE", calls) as client:
        deployment_id = import_person_model(
            model, "v1", client=client, poll_seconds=0
        )

    assert deployment_id == "deployment-1"
    assert calls[-1] == "PUT /api/v1/model-assignments"
    assert calls.index("POST /api/v1/deployments/deployment-1/activate") < len(calls) - 1


def test_import_never_assigns_when_worker_activation_fails(tmp_path: Path) -> None:
    from scripts.import_person_model import import_person_model

    model = tmp_path / "person.onnx"
    model.write_bytes(b"known model")
    calls: list[str] = []
    with _client_for("ERROR", calls) as client, pytest.raises(
        RuntimeError, match="MODEL_ACTIVATION_FAILED:BAD_ONNX"
    ):
        import_person_model(model, "v1", client=client, poll_seconds=0)

    assert not any(call.startswith("PUT /api/v1/model-assignments") for call in calls)
