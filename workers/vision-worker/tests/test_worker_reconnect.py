import sys

import httpx
import pytest
import vision_worker.main as worker_main
from vision_worker.model_runtime import RuntimeCapabilities


def test_managed_worker_retries_after_api_restart_and_resumes_camera_sync(monkeypatch):
    assigned_calls = []
    camera_calls = []
    sleeps = []

    def assigned(_):
        assigned_calls.append(True)
        if len(assigned_calls) == 1:
            raise httpx.ConnectError("API temporarily restarting")
        return []

    def cameras(_):
        camera_calls.append(True)
        return []

    def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setenv("FR_WORKER_INTERNAL_TOKEN", "test-worker")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "--control-api-url",
            "http://api",
            "--feature-config",
            "configuration/examples/face-recognition.example.json",
        ],
    )
    monkeypatch.setattr(worker_main, "discover_runtime_capabilities", RuntimeCapabilities.cpu_only)
    monkeypatch.setattr(worker_main.HttpDeploymentProvider, "assigned", assigned)
    monkeypatch.setattr(worker_main.HttpDeploymentProvider, "pending", lambda _: [])
    monkeypatch.setattr(worker_main.HttpActiveCameraProvider, "fetch", cameras)
    monkeypatch.setattr(worker_main.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        worker_main.main()
    assert len(assigned_calls) == 2
    assert len(camera_calls) == 1
    assert sleeps == [2.0, 2.0]
