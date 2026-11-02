"""Bound upload ingress before Starlette spools multipart files to disk."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from control_api.face_image_storage import MAX_FILE_BYTES, MAX_UPLOAD_FILES

MAX_REQUEST_BYTES = MAX_FILE_BYTES * MAX_UPLOAD_FILES + 1024 * 1024


def _is_upload(method: str, path: str) -> bool:
    return (
        method == "POST"
        and (
            (
                path.startswith("/api/v1/persons/")
                and path.rstrip("/").endswith("/face-images")
            )
            or path.rstrip("/") == "/api/v1/me/face-images"
        )
    )


class FaceUploadBodyLimit:
    def __init__(
        self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BYTES, upload_path: str | None = None
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_path = upload_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        matches = (
            scope["method"] == "POST" and scope["path"].rstrip("/") == self.upload_path
            if self.upload_path
            else _is_upload(scope["method"], scope["path"])
        )
        if not matches:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                length = int(raw_length)
                if length < 0:
                    raise ValueError
            except ValueError:
                await JSONResponse({"detail": "INVALID_CONTENT_LENGTH"}, status_code=400)(
                    scope,
                    receive,
                    send,
                )
                return
            if length > self.max_bytes:
                await JSONResponse({"detail": "UPLOAD_REQUEST_TOO_LARGE"}, status_code=413)(
                    scope,
                    receive,
                    send,
                )
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # The multipart parser closes already-open temp files on this error.
                    raise MultiPartException("UPLOAD_REQUEST_TOO_LARGE")
            return message

        await self.app(scope, limited_receive, send)


class FaceUploadRoute(APIRoute):
    matches_upload = staticmethod(_is_upload)
    max_files = MAX_UPLOAD_FILES
    max_fields = 1

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if not self.matches_upload(request.method, request.url.path):
                return await original(request)
            try:
                # Parse once with the actual file-count bound; FastAPI reuses the cached form.
                form = await request.form(
                    max_files=self.max_files,
                    max_fields=self.max_fields,
                    max_part_size=1024,
                )
            except StarletteHTTPException as error:
                if error.detail == "UPLOAD_REQUEST_TOO_LARGE":
                    raise HTTPException(413, "UPLOAD_REQUEST_TOO_LARGE") from error
                category = (
                    "TOO_MANY_FILES"
                    if "Too many files" in str(error.detail)
                    else ("INVALID_MULTIPART")
                )
                raise HTTPException(422, category) from error
            try:
                return await original(request)
            finally:
                await form.close()

        return handler
