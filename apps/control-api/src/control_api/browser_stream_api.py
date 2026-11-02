from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from control_api.browser_stream import MAX_FRAME_BYTES, BrowserStreamError, BrowserStreamStore
from control_api.cameras import CameraRecord, CameraService


def create_browser_stream_router(
    cameras: CameraService,
    streams: BrowserStreamStore,
    worker_token: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Realtime browser webcam"])

    def require(camera_id: str) -> CameraRecord:
        try:
            camera = cameras.require(camera_id)
        except KeyError as error:
            raise HTTPException(404, "CAMERA_NOT_FOUND") from error
        if camera.source_type != "webcam" or camera.lifecycle != "ACTIVE" or not camera.enabled:
            raise HTTPException(409, "WEBCAM_CAMERA_NOT_ACTIVE")
        return camera

    def session_error(error: BrowserStreamError) -> HTTPException:
        category = str(error)
        status = (
            403
            if category == "WEBCAM_SESSION_UNAUTHORIZED"
            else (
                410
                if category == "WEBCAM_SESSION_ENDED"
                else 413
                if category == "WEBCAM_FRAME_TOO_LARGE"
                else 422
                if category == "INVALID_WEBCAM_FRAME"
                else 409
            )
        )
        return HTTPException(status, category)

    @router.post("/cameras/{camera_id}/webcam/session", status_code=201)
    def start(camera_id: str) -> dict[str, object]:
        camera = require(camera_id)
        try:
            session = streams.start(camera_id, camera.revision)
        except BrowserStreamError as error:
            raise session_error(error) from error
        return {"session_id": session.id, "token": session.token, "expires_in_seconds": 10}

    @router.delete("/cameras/{camera_id}/webcam/session", status_code=204)
    def stop(camera_id: str, x_webcam_token: Annotated[str, Header()]) -> Response:
        camera = require(camera_id)
        try:
            streams.stop(camera_id, camera.revision, x_webcam_token)
        except BrowserStreamError as error:
            raise session_error(error) from error
        return Response(status_code=204)

    @router.post("/cameras/{camera_id}/webcam/frames")
    async def push(
        camera_id: str,
        request: Request,
        x_webcam_token: Annotated[str, Header()],
    ) -> dict[str, object]:
        camera = require(camera_id)
        try:
            streams.authorize(camera_id, camera.revision, x_webcam_token)
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > MAX_FRAME_BYTES:
                    raise BrowserStreamError("WEBCAM_FRAME_TOO_LARGE")
                payload.extend(chunk)
            frame = await run_in_threadpool(
                streams.push,
                camera_id,
                camera.revision,
                x_webcam_token,
                bytes(payload),
            )
        except BrowserStreamError as error:
            raise session_error(error) from error
        return {"sequence": frame.sequence, "session_id": frame.session_id}

    @router.get("/internal/cameras/{camera_id}/webcam/frame")
    def latest(
        camera_id: str,
        session_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        x_worker_token: Annotated[str | None, Header()] = None,
    ) -> Response:
        if worker_token is None or x_worker_token != worker_token:
            raise HTTPException(403, "worker authorization required")
        camera = require(camera_id)
        try:
            frame = streams.frame(camera_id, camera.revision, session_id, after)
        except BrowserStreamError as error:
            raise session_error(error) from error
        if frame is None:
            return Response(status_code=204)
        return Response(
            frame.payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Sequence": str(frame.sequence),
                "X-Stream-Session-Id": frame.session_id,
                "X-Frame-Timestamp-Ms": str(frame.timestamp_ms),
            },
        )

    return router
