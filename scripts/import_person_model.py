"""Import, validate, activate, and assign a YOLOX ONNX person detector."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"API_{response.status_code}:{response.text[:300]}")
    if response.status_code == 204:
        return None
    return response.json()


def import_person_model(
    path: Path,
    version: str,
    api_url: str = "http://127.0.0.1:8000",
    *,
    decode_mode: str = "auto",
    person_class_id: int = 0,
    confidence: float = 0.3,
    wait_seconds: float = 90.0,
    poll_seconds: float = 1.0,
    client: httpx.Client | None = None,
) -> str:
    model_path = path.expanduser().resolve()
    if not model_path.is_file() or model_path.suffix.lower() != ".onnx":
        raise ValueError("ONNX_FILE_REQUIRED")
    timeout = httpx.Timeout(30.0, connect=5.0)
    owned_client = client is None
    session = client or httpx.Client(base_url=api_url.rstrip("/"), timeout=timeout)
    try:
        with model_path.open("rb") as model_file:
            uploaded = _request(
                session,
                "POST",
                "/api/v1/person-models",
                data={
                    "version": version,
                    "decode_mode": decode_mode,
                    "person_class_id": str(person_class_id),
                    "confidence": str(confidence),
                },
                files={"file": (model_path.name, model_file, "application/octet-stream")},
            )
        version_id = uploaded["id"]
        person_models = _request(session, "GET", "/api/v1/person-models")["items"]
        item = next((entry for entry in person_models if entry["id"] == version_id), None)
        if item is None:
            raise RuntimeError("IMPORTED_VERSION_NOT_FOUND")

        model_type = "PERSON_DETECTOR"
        active_id = _request(session, "GET", "/api/v1/model-assignments")["assignments"].get(
            model_type
        )
        deployment = next(
            (
                candidate
                for candidate in item["deployments"]
                if candidate["id"] == active_id and candidate["status"] == "ACTIVE"
            ),
            None,
        )
        if deployment is None:
            deployment = next(
                (
                    candidate
                    for candidate in item["deployments"]
                    if candidate["status"] in {"ACTIVATION_PENDING", "ACTIVE"}
                ),
                None,
            )
        if deployment is None:
            deployment = _request(
                session,
                "POST",
                f"/api/v1/model-versions/{version_id}/deployments",
                json={"backend": "ONNX_CPU", "precision": "FP32"},
            )
        deployment_id = deployment["id"]
        if deployment["status"] not in {"ACTIVE", "ACTIVATION_PENDING"}:
            _request(session, "POST", f"/api/v1/deployments/{deployment_id}/activate")

        deadline = time.monotonic() + wait_seconds
        last_state = "WAITING_FOR_WORKER"
        while time.monotonic() <= deadline:
            runtime = _request(session, "GET", f"/api/v1/deployments/{deployment_id}/runtime")
            last_state = str(runtime.get("error_category") or runtime.get("state") or "UNKNOWN")
            if runtime.get("configured_status") == "ACTIVE" and runtime.get("state") == "ACTIVE":
                assignments = _request(
                    session,
                    "PUT",
                    "/api/v1/model-assignments",
                    json={"model_type": model_type, "deployment_id": deployment_id},
                )["assignments"]
                if assignments.get(model_type) != deployment_id:
                    raise RuntimeError("MODEL_ASSIGNMENT_NOT_CONFIRMED")
                return deployment_id
            if runtime.get("configured_status") == "ERROR" or runtime.get("state") == "ERROR":
                raise RuntimeError(f"MODEL_ACTIVATION_FAILED:{last_state}")
            time.sleep(poll_seconds)
        raise TimeoutError(f"WORKER_ACTIVATION_TIMEOUT:{last_state}")
    finally:
        if owned_client:
            session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--decode-mode", default="auto")
    parser.add_argument("--person-class-id", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.3)
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    args = parser.parse_args(argv)
    try:
        deployment_id = import_person_model(
            args.path,
            args.version,
            args.api_url,
            decode_mode=args.decode_mode,
            person_class_id=args.person_class_id,
            confidence=args.confidence,
            wait_seconds=args.wait_seconds,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Activated and assigned PERSON_DETECTOR deployment {deployment_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
