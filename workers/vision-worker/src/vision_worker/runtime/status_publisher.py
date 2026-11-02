import logging
import os
from time import monotonic
from typing import Protocol

import httpx
from fr_contracts.runtime_status import RuntimeStatus


class RuntimeStatusPublisher(Protocol):
    def publish(self, status: RuntimeStatus) -> None: ...


class BestEffortRuntimeStatusPublisher:
    """Telemetry outages never terminate camera ingestion or inference."""

    def __init__(self, publisher: RuntimeStatusPublisher) -> None:
        self._publisher = publisher
        self._last_warning = float("-inf")

    def publish(self, status: RuntimeStatus) -> None:
        try:
            self._publisher.publish(status)
        except (httpx.HTTPError, OSError):
            now = monotonic()
            if now - self._last_warning >= 30:
                logging.getLogger(__name__).warning("RUNTIME_STATUS_PUBLISH_FAILED")
                self._last_warning = now


class HttpRuntimeStatusPublisher:
    """Publishes worker snapshots to the control API's internal runtime boundary."""

    def __init__(
        self,
        endpoint: str,
        timeout_seconds: float = 2.0,
        client: httpx.Client | None = None,
        *,
        worker_token: str | None = None,
    ) -> None:
        self._endpoint = endpoint
        configured_token = worker_token or os.getenv("FR_WORKER_INTERNAL_TOKEN")
        if not configured_token:
            raise ValueError("FR_WORKER_INTERNAL_TOKEN is required for runtime status publishing")
        self._worker_token: str = configured_token
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def publish(self, status: RuntimeStatus) -> None:
        response = self._client.put(
            self._endpoint,
            json=status.model_dump(mode="json"),
            headers={"X-Worker-Token": self._worker_token},
        )
        response.raise_for_status()
