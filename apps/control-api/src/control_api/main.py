import asyncio
import os
from pathlib import Path
from time import monotonic

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi import status as http_status
from fastapi.exceptions import RequestValidationError
from fr_config.models import CameraConfig, FaceRecognitionConfig
from fr_contracts.messages import MessageEnvelope
from fr_contracts.runtime_status import RuntimeStatus
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from control_api.auth_api import SESSION_COOKIE, create_auth_router
from control_api.auth_domain import AuthService, Principal
from control_api.auth_postgres import PostgresAuthRepository
from control_api.browser_stream import BrowserStreamStore
from control_api.browser_stream_api import create_browser_stream_router
from control_api.camera_bootstrap import build_camera_persistence
from control_api.camera_preview import PreviewStore, create_camera_preview_router
from control_api.cameras import (
    ActiveCameraConfig,
    CameraCreate,
    CameraPublic,
    CameraRevisionConflict,
    CameraService,
    CameraUpdate,
)
from control_api.class_api import create_class_router
from control_api.class_domain import (
    ClassRepository,
    ClassService,
    InMemoryClassRepository,
    VerifiedEventQuery,
)
from control_api.class_postgres import PostgresClassRepository
from control_api.database_migrations import apply_database_migrations
from control_api.face_registry import FaceRegistryService, build_face_registry
from control_api.face_registry_api import create_face_registry_router
from control_api.face_upload_limits import FaceUploadBodyLimit
from control_api.model_api import create_model_router
from control_api.model_bootstrap import build_model_registry
from control_api.model_registry import ModelRegistryService
from control_api.object_storage import ObjectStorage, ObjectStorageError
from control_api.person_model_api import create_person_model_router
from control_api.recognition_events import RecognitionEventStore
from control_api.runtime_status import InMemoryRuntimeStatusStore, RuntimeStatusStore
from control_api.simple_face_recognition import RegisteredFaceComparison
from control_api.simple_recognition_api import FaceComparison, create_simple_recognition_router
from control_api.storage_bootstrap import build_configured_object_storage
from control_api.student_api import create_student_router
from control_api.vector_index import VectorIndexService
from control_api.vector_index_api import create_vector_index_router

WS_SESSION_RECHECK_SECONDS = 5.0


class ValidationResponse(BaseModel):
    valid: bool
    config: FaceRecognitionConfig


class RuntimeStatusResponse(BaseModel):
    items: list[RuntimeStatus]


class BrowserAuthBoundary:
    """Pure ASGI middleware so auth checks do not buffer or wrap streaming uploads."""

    def __init__(self, app: ASGIApp, auth: AuthService | None) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path = request.url.path
        if not path.startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return
        if path == "/api/v1/auth/login":
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin is None or origin.rstrip("/").lower() != expected_origin.rstrip("/").lower():
                await JSONResponse(
                    status_code=403, content={"detail": "same-origin request required"}
                )(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if path.startswith("/api/v1/internal/") or (
            path == "/api/v1/runtime/status" and request.method == "PUT"
        ):
            await self.app(scope, receive, send)
            return
        if self.auth is None:
            await JSONResponse(
                status_code=503, content={"detail": "authentication service unavailable"}
            )(scope, receive, send)
            return
        session_token = request.cookies.get(SESSION_COOKIE)
        principal = self.auth.resolve_session(session_token)
        if principal is None:
            await JSONResponse(status_code=401, content={"detail": "authentication required"})(
                scope, receive, send
            )
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin is None or origin.rstrip("/").lower() != expected_origin.rstrip("/").lower():
                await JSONResponse(
                    status_code=403, content={"detail": "same-origin request required"}
                )(scope, receive, send)
                return
            csrf = request.headers.get("x-csrf-token")
            if not self.auth.verify_csrf(csrf, session_token):
                await JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})(
                    scope, receive, send
                )
                return
        required_role = "ADMIN"
        if path == "/api/v1/teacher" or path.startswith("/api/v1/teacher/"):
            required_role = "TEACHER"
        elif path == "/api/v1/me" or path.startswith("/api/v1/me/"):
            required_role = "STUDENT"
        elif path == "/api/v1/auth" or path.startswith("/api/v1/auth/"):
            required_role = "ANY"
        if required_role != "ANY" and principal.role != required_role:
            await JSONResponse(status_code=403, content={"detail": "role access denied"})(
                scope, receive, send
            )
            return
        request.state.principal = principal
        await self.app(scope, receive, send)


def create_app(
    runtime_status_store: RuntimeStatusStore | None = None,
    camera_service: CameraService | None = None,
    internal_camera_token: str | None = None,
    model_registry_service: ModelRegistryService | None = None,
    vector_index_service: VectorIndexService | None = None,
    face_registry_service: FaceRegistryService | None = None,
    face_comparison_service: FaceComparison | None = None,
    auth_service: AuthService | None = None,
    class_repository: ClassRepository | None = None,
    class_event_query: VerifiedEventQuery | None = None,
) -> FastAPI:
    status_store = runtime_status_store or InMemoryRuntimeStatusStore()
    dsn = os.getenv("FR_POSTGRES_DSN")
    if dsn:
        from control_api.camera_postgres import connect_postgres

        migration_connection = connect_postgres(dsn)
        try:
            apply_database_migrations(
                migration_connection,
                Path(os.getenv("FR_MIGRATIONS_DIR", "infrastructure/database/migrations")),
            )
        finally:
            migration_connection.close()  # type: ignore[attr-defined]
    if camera_service is None:
        persistence = build_camera_persistence()
        cameras = persistence.service
        persistence_mode = persistence.mode
    else:
        cameras = camera_service
        persistence_mode = "injected"
    worker_token = internal_camera_token or os.getenv("FR_WORKER_INTERNAL_TOKEN")
    auth = auth_service
    if auth is None and os.getenv("FR_POSTGRES_DSN"):
        rate_limit_key = os.getenv("FR_AUTH_RATE_LIMIT_KEY", "")
        if len(rate_limit_key) < 32:
            raise RuntimeError(
                "FR_AUTH_RATE_LIMIT_KEY must be configured with at least 32 characters"
            )
        auth = AuthService(
            PostgresAuthRepository(os.environ["FR_POSTGRES_DSN"]),
            rate_limit_key=rate_limit_key.encode(),
        )

    def browser_revision(camera_id: str) -> str | None:
        try:
            camera = cameras.require(camera_id)
        except KeyError:
            return None
        return (
            camera.revision
            if (camera.source_type == "webcam" and camera.enabled and camera.lifecycle == "ACTIVE")
            else None
        )

    browser_streams = BrowserStreamStore(revision_resolver=browser_revision)
    camera_previews = PreviewStore()
    object_storage_health_checks: list[ObjectStorage] = []
    event_root = Path(os.getenv("FR_EVENT_STORE_ROOT", "data/recognition-events"))
    event_dsn = os.getenv("FR_POSTGRES_DSN")
    event_storage = None
    if event_dsn:
        from control_api.camera_postgres import connect_postgres

        event_storage = build_configured_object_storage("MINIO_EVIDENCE_BUCKET", "evidence")
        if event_storage is None:
            raise RuntimeError("MinIO evidence storage is required with FR_POSTGRES_DSN")
        recognition_events = RecognitionEventStore(
            event_root, connection=connect_postgres(event_dsn), storage=event_storage
        )
        object_storage_health_checks.append(event_storage)
    else:
        recognition_events = RecognitionEventStore(event_root)
    classes = class_repository
    if classes is None:
        classes = PostgresClassRepository(dsn) if dsn else InMemoryClassRepository()
    class_service = ClassService(classes, cameras, class_event_query or recognition_events)
    face_objects = None
    model_objects = None
    if face_registry_service is None:
        face_objects = build_configured_object_storage("MINIO_FACE_IMAGES_BUCKET", "face-images")
        if face_objects is not None:
            object_storage_health_checks.append(face_objects)
    if model_registry_service is None:
        model_objects = build_configured_object_storage("MINIO_MODELS_BUCKET", "models")
        if model_objects is not None:
            object_storage_health_checks.append(model_objects)
    face_registry = face_registry_service or build_face_registry(face_objects)
    face_comparison = face_comparison_service or RegisteredFaceComparison(face_registry)
    models = model_registry_service or build_model_registry(model_objects)
    vector_index = vector_index_service
    if vector_index is None and os.getenv("FR_POSTGRES_DSN"):
        from control_api.camera_postgres import connect_postgres

        vector_index = VectorIndexService.from_postgres(
            connect_postgres(os.environ["FR_POSTGRES_DSN"]),
            Path(os.getenv("FR_VECTOR_INDEX_ROOT", "data/vector-index")),
        )
    app = FastAPI(
        title="Face Recognition Control API",
        version="0.1.0",
        description="Configuration-first control plane; inference adapters are stubbed.",
    )
    app.state.auth_service = auth
    app.state.class_service = class_service
    app.state.recognition_events = recognition_events
    app.state.face_registry = face_registry
    app.state.models = models
    app.state.vector_index = vector_index

    def close_owned_resources() -> None:
        recognition_events.close()
        if model_registry_service is None:
            connection = getattr(models.repository, "_connection", None)
            if connection is not None:
                connection.close()
        if vector_index_service is None and vector_index is not None:
            connection = getattr(vector_index._repository, "_connection", None)
            if connection is not None:
                connection.close()

    app.add_middleware(BrowserAuthBoundary, auth=auth)

    api = APIRouter(prefix="/api/v1")
    app.add_middleware(FaceUploadBodyLimit)
    app.add_middleware(
        FaceUploadBodyLimit,
        max_bytes=models.max_upload_bytes + 64 * 1024,
        upload_path="/api/v1/person-models",
    )

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "request validation failed",
                "error_category": "VALIDATION_ERROR",
            },
        )

    @app.get("/health", response_model=None)
    def health() -> dict[str, object] | Response:
        payload: dict[str, object] = {
            "service": "face-recognition-control-api",
            "status": "ok",
            "external_dependencies": "configured"
            if persistence_mode == "postgresql"
            else "optional",
            "camera_persistence": persistence_mode,
        }
        try:
            for storage in object_storage_health_checks:
                storage.health_check()
        except ObjectStorageError:
            payload["status"] = "unhealthy"
            payload["object_storage"] = "unavailable"
            return JSONResponse(status_code=503, content=payload)
        if object_storage_health_checks:
            payload["object_storage"] = "ready"
        return payload

    @api.post("/face-recognition/configs/validate", response_model=ValidationResponse)
    def validate_config(config: FaceRecognitionConfig) -> ValidationResponse:
        return ValidationResponse(valid=True, config=config)

    @api.post("/cameras/validate", response_model=CameraConfig)
    def validate_camera_config(config: CameraConfig) -> CameraConfig:
        return config

    @api.post("/cameras", response_model=CameraPublic, status_code=http_status.HTTP_201_CREATED)
    def create_camera(request: CameraCreate) -> CameraPublic:
        return cameras.create(request)

    @api.get("/cameras")
    def list_cameras() -> dict[str, list[CameraPublic]]:
        return {"items": cameras.public_list()}

    @api.get("/cameras/{camera_id}", response_model=CameraPublic)
    def get_camera(camera_id: str) -> CameraPublic:
        try:
            return cameras.get_public(camera_id)
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error

    @api.patch("/cameras/{camera_id}", response_model=CameraPublic)
    def update_camera(camera_id: str, request: CameraUpdate) -> CameraPublic:
        try:
            previous = cameras.require(camera_id)
            updated = cameras.update(camera_id, request)
            if previous.lifecycle == "ACTIVE" and updated.lifecycle != "ACTIVE":
                session, track_ids = camera_previews.stop_camera(camera_id)
                recognition_events.mark_tracks_absent(camera_id, session, track_ids)
            return updated
        except CameraRevisionConflict as error:
            raise HTTPException(409, "CAMERA_REVISION_CONFLICT") from error
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error
        except ValueError as error:
            raise HTTPException(422, "INVALID_CAMERA_SOURCE") from error

    @api.post("/cameras/{camera_id}/test-connection")
    def test_camera(camera_id: str) -> object:
        try:
            return cameras.test_connection(camera_id)
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error

    @api.delete("/cameras/{camera_id}", status_code=204)
    def delete_camera(camera_id: str, expected_revision: str) -> None:
        try:
            cameras.delete(camera_id, expected_revision)
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        session, track_ids = camera_previews.stop_camera(camera_id)
        browser_streams.revoke_camera(camera_id)
        recognition_events.mark_tracks_absent(camera_id, session, track_ids)

    @api.post("/cameras/{camera_id}/activate", response_model=CameraPublic)
    def activate_camera(camera_id: str) -> CameraPublic:
        try:
            return cameras.activate(camera_id)
        except CameraRevisionConflict as error:
            raise HTTPException(409, "CAMERA_REVISION_CONFLICT") from error
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error

    @api.get("/internal/cameras/active")
    def active_cameras(
        x_worker_token: str | None = Header(default=None),
    ) -> dict[str, list[ActiveCameraConfig]]:
        if worker_token is None or x_worker_token != worker_token:
            raise HTTPException(403, "worker authorization required")
        items = []
        for camera in cameras.active_list():
            if camera.source_type == "webcam":
                session = browser_streams.current(camera.id, camera.revision)
                if session is None:
                    continue
                camera = camera.model_copy(update={"stream_session_id": session.id})
            items.append(camera)
        return {"items": items}

    @api.get("/cameras/{camera_id}/runtime", response_model=RuntimeStatusResponse)
    def camera_runtime(camera_id: str) -> RuntimeStatusResponse:
        try:
            cameras.require(camera_id)
        except KeyError as error:
            raise HTTPException(404, "camera not found") from error
        return RuntimeStatusResponse(
            items=[item for item in status_store.list() if item.camera_id == camera_id]
        )

    @api.get("/runtime/status", response_model=RuntimeStatusResponse)
    def runtime_status() -> RuntimeStatusResponse:
        return RuntimeStatusResponse(items=status_store.list())

    @api.put("/runtime/status", response_model=RuntimeStatus)
    def update_runtime_status(
        status: RuntimeStatus,
        x_worker_token: str | None = Header(default=None),
    ) -> RuntimeStatus:
        if worker_token is None or x_worker_token != worker_token:
            raise HTTPException(403, "worker authorization required")
        status_store.update(status)
        return status

    @api.websocket("/ws")
    async def websocket_status(
        websocket: WebSocket,
    ) -> None:
        principal: Principal | None = None
        if auth is not None:
            origin = websocket.headers.get("origin")
            scheme = "https" if websocket.url.scheme in {"wss", "https"} else "http"
            expected_origin = f"{scheme}://{websocket.url.netloc}"
            if origin and origin.rstrip("/").lower() == expected_origin.rstrip("/").lower():
                principal = await asyncio.to_thread(
                    auth.resolve_session, websocket.cookies.get(SESSION_COOKIE)
                )
        if principal is not None and principal.role != "ADMIN":
            principal = None
        if principal is None:
            await websocket.close(code=1008, reason="browser session required")
            return
        session_token = websocket.cookies.get(SESSION_COOKIE)
        await websocket.accept()
        message = MessageEnvelope(type="system.ready", payload={"mode": "scaffold"})
        await websocket.send_json(message.model_dump(mode="json"))
        sent: dict[tuple[str, str], str] = {}
        last_session_check = monotonic()
        while True:
            if monotonic() - last_session_check >= WS_SESSION_RECHECK_SECONDS:
                # Revocation is observed within five seconds; keep synchronous DB I/O off-loop.
                current = (
                    await asyncio.to_thread(auth.resolve_session, session_token)
                    if auth is not None
                    else None
                )
                if current is None or current.role != "ADMIN":
                    await websocket.close(code=1008, reason="browser session expired or revoked")
                    return
                last_session_check = monotonic()
            for status in status_store.list():
                key = (status.worker_id, status.camera_id)
                serialized = status.model_dump_json()
                if sent.get(key) == serialized:
                    continue
                status_message = MessageEnvelope(
                    type="runtime.status",
                    camera_id=status.camera_id,
                    payload=status.model_dump(mode="json"),
                )
                await websocket.send_json(status_message.model_dump(mode="json"))
                sent[key] = serialized
            await asyncio.sleep(0.05)

    if auth is None:

        @api.post("/auth/login")
        def login_unavailable() -> Response:
            return JSONResponse(
                status_code=503, content={"detail": "authentication service unavailable"}
            )

    app.include_router(api)
    if auth is not None:
        app.include_router(create_auth_router(auth))
    app.include_router(create_class_router(class_service))
    app.include_router(create_model_router(models, worker_token))
    app.include_router(create_person_model_router(models))
    app.include_router(create_vector_index_router(vector_index))
    app.include_router(create_face_registry_router(face_registry))
    app.include_router(
        create_student_router(face_registry, class_service, recognition_events, face_comparison)
    )
    app.include_router(create_browser_stream_router(cameras, browser_streams, worker_token))
    app.include_router(
        create_camera_preview_router(cameras, worker_token, camera_previews, recognition_events)
    )
    app.include_router(
        create_simple_recognition_router(
            face_comparison, cameras, camera_previews, browser_streams, recognition_events
        )
    )
    # Stop recognition/evidence producers before closing their repositories.
    app.router.on_shutdown.append(close_owned_resources)
    return app


app = create_app()
