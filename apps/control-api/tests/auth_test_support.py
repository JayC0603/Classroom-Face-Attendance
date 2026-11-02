"""Admin-authenticated client helpers for legacy API tests after the RBAC gate."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from control_api.auth_domain import AuthService, InMemoryAuthRepository
from control_api.main import create_app as _create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient as _TestClient

TEST_ADMIN_EMAIL = "test-admin@example.test"
TEST_ADMIN_PASSWORD = "Test administrator password 9!"


def create_app(*args: Any, **kwargs: Any) -> FastAPI:
    auth = kwargs.get("auth_service")
    if auth is None:
        auth = AuthService(InMemoryAuthRepository())
        auth.bootstrap_admin(TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        kwargs["auth_service"] = auth
    return _create_app(*args, **kwargs)


class TestClient(_TestClient):
    """Test client that explicitly injects and uses a test-only ADMIN principal."""

    def __init__(self, app: FastAPI, *args: Any, **kwargs: Any) -> None:
        super().__init__(app, *args, **kwargs)
        auth: AuthService | None = getattr(app.state, "auth_service", None)
        if auth is None:
            return
        result = auth.login(TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, "127.0.0.1")
        origin = urlsplit(str(self.base_url))
        self.headers.update(
            {
                "Origin": f"{origin.scheme}://{origin.netloc}",
                "X-CSRF-Token": result.csrf_token,
            }
        )
        self.cookies.set("fr_session", result.session_token, path="/api/v1")
