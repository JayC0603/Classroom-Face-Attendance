"""Browser authentication and administrative account endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from control_api.auth_domain import AuthService, Principal, User

SESSION_COOKIE = "fr_session"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    role: str
    person_id: str | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


def principal_from_request(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, "authentication required")
    return principal


def safe_user(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "person_id": user.person_id,
        "disabled": user.disabled,
    }


def _set_session_cookie(
    response: Response, request: Request, token: str, expires_at: datetime
) -> None:
    host = (request.url.hostname or "").lower()
    local_http = request.url.scheme == "http" and host in {
        "localhost",
        "127.0.0.1",
        "::1",
        "testserver",
    }
    secure = not local_http
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        expires=expires_at,
        path="/api/v1",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api/v1", httponly=True, samesite="strict")


def create_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response) -> dict[str, object]:
        remote = request.client.host if request.client else "unknown"
        try:
            result = service.login(body.email, body.password, remote)
        except ValueError as error:
            if str(error) == "LOGIN_RATE_LIMITED":
                raise HTTPException(429, "login temporarily unavailable") from error
            raise HTTPException(401, "invalid email or password") from error
        _set_session_cookie(response, request, result.session_token, result.expires_at)
        return {
            "user": result.principal.__dict__,
            "csrf_token": result.csrf_token,
            "expires_at": result.expires_at,
        }

    @router.get("/auth/me")
    def me(request: Request) -> dict[str, object]:
        session_token = request.cookies.get(SESSION_COOKIE)
        current = service.current(session_token)
        if current is None:
            raise HTTPException(401, "authentication required")
        principal, csrf_token = current
        return {"user": principal.__dict__, "csrf_token": csrf_token}

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(request: Request, response: Response) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            service.logout(token)
        _clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.post("/auth/change-password")
    def change_password(
        body: PasswordChange, request: Request, response: Response
    ) -> dict[str, object]:
        principal = principal_from_request(request)
        token = request.cookies.get(SESSION_COOKIE)
        if token is None:
            raise HTTPException(401, "authentication required")
        try:
            result = service.change_password(principal, token, body.old_password, body.new_password)
        except ValueError as error:
            raise HTTPException(
                400 if str(error) == "PASSWORD_POLICY" else 401, "password change failed"
            ) from error
        _set_session_cookie(response, request, result.session_token, result.expires_at)
        return {
            "user": result.principal.__dict__,
            "csrf_token": result.csrf_token,
            "expires_at": result.expires_at,
        }

    @router.post("/admin/accounts", status_code=status.HTTP_201_CREATED)
    def create_account(body: AccountCreate, request: Request) -> dict[str, object]:
        principal = principal_from_request(request)
        try:
            user = service.create_account(body.email, body.password, body.role, body.person_id)
        except ValueError as error:
            code = str(error)
            status_code = 409 if code == "ACCOUNT_ALREADY_EXISTS" else 422
            raise HTTPException(status_code, code) from error
        audit = getattr(service.repository, "audit", None)
        if callable(audit):
            audit(principal.user_id, "ACCOUNT_CREATED", user.id)
        return safe_user(user)

    @router.get("/admin/accounts")
    def list_accounts(request: Request) -> dict[str, list[dict[str, object]]]:
        principal_from_request(request)
        return {"items": [safe_user(user) for user in service.list_accounts()]}

    @router.post("/admin/accounts/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
    def disable_account(user_id: str, request: Request) -> Response:
        principal = principal_from_request(request)
        if user_id == principal.user_id:
            raise HTTPException(422, "cannot disable current administrator")
        if not service.disable_account(user_id):
            raise HTTPException(404, "account not found")
        audit = getattr(service.repository, "audit", None)
        if callable(audit):
            audit(principal.user_id, "ACCOUNT_DISABLED", user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/admin/accounts/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
    def reset_password(user_id: str, body: PasswordReset, request: Request) -> Response:
        principal_from_request(request)
        if not service.reset_password(user_id, body.password):
            raise HTTPException(404, "account not found")
        audit = getattr(service.repository, "audit", None)
        if callable(audit):
            audit(principal_from_request(request).user_id, "ACCOUNT_PASSWORD_RESET", user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
