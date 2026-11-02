"""Bounded ephemeral worker previews; no camera images are persisted."""

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from threading import Lock
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from control_api.cameras import CameraService

MAX_PREVIEW_BYTES = 256 * 1024

UnitFloat = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class PersonDetection(BaseModel):
    box: tuple[UnitFloat, UnitFloat, UnitFloat, UnitFloat]
    confidence: UnitFloat
    track_id: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def geometry(self) -> "PersonDetection":
        x, y, width, height = self.box
        if width <= 0 or height <= 0 or x + width > 1.0002 or y + height > 1.0002:
            raise ValueError("INVALID_PERSON_BOX")
        return self


class PersonDetections(BaseModel):
    items: list[PersonDetection] = Field(max_length=25)


@dataclass(frozen=True)
class PreviewSnapshot:
    revision: str
    received: float
    payload: bytes
    sequence: int
    session: str
    persons: tuple[PersonDetection, ...] = ()
    person_deployment_id: str = ""


class PreviewStore:
    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._frames: dict[str, PreviewSnapshot] = {}
        self._lock = Lock()
        self._sequence = 0
        self._missing_since: dict[tuple[str, str, int], float] = {}

    def _purge(self) -> None:
        now = self._clock()
        self._frames = {
            key: value for key, value in self._frames.items() if now - value.received < 3
        }

    def put(
        self,
        camera_id: str,
        revision: str,
        payload: bytes,
        session: str = "",
        persons: tuple[PersonDetection, ...] = (),
        person_deployment_id: str = "",
    ) -> set[int]:
        with self._lock:
            self._purge()
            if camera_id not in self._frames and len(self._frames) >= 16:
                raise HTTPException(429, "PREVIEW_CAPACITY_REACHED")
            self._sequence += 1
            previous = self._frames.get(camera_id)
            current_ids = {item.track_id for item in persons if item.track_id is not None}
            ended: set[int] = set()
            if previous is not None and previous.session == session and person_deployment_id:
                previous_ids = {
                    item.track_id for item in previous.persons if item.track_id is not None
                }
                now = self._clock()
                for track_id in current_ids:
                    self._missing_since.pop((camera_id, session, track_id), None)
                for track_id in previous_ids - current_ids:
                    self._missing_since.setdefault((camera_id, session, track_id), now)
                for key, since in list(self._missing_since.items()):
                    if (
                        key[0] == camera_id
                        and key[1] == session
                        and key[2] not in current_ids
                        and now - since >= 1.5
                    ):
                        ended.add(key[2])
                        self._missing_since.pop(key, None)
            elif previous is not None and previous.session != session:
                self._missing_since = {
                    key: value for key, value in self._missing_since.items() if key[0] != camera_id
                }
            self._frames[camera_id] = PreviewSnapshot(
                revision,
                self._clock(),
                payload,
                self._sequence,
                session,
                persons,
                person_deployment_id,
            )
            return ended

    def get(self, camera_id: str, revision: str) -> bytes | None:
        frame = self.snapshot(camera_id, revision)
        return frame.payload if frame else None

    def snapshot(self, camera_id: str, revision: str) -> PreviewSnapshot | None:
        with self._lock:
            self._purge()
            frame = self._frames.get(camera_id)
            return frame if frame and frame.revision == revision else None

    def stop_camera(self, camera_id: str) -> tuple[str, set[int]]:
        with self._lock:
            frame = self._frames.pop(camera_id, None)
            self._missing_since = {
                key: value for key, value in self._missing_since.items() if key[0] != camera_id
            }
            if frame is None:
                return "", set()
            return frame.session, {
                item.track_id for item in frame.persons if item.track_id is not None
            }


def validate_preview(payload: bytes) -> None:
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG" or not (0 < image.width <= 640 and 0 < image.height <= 480):
                raise ValueError
            image.verify()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise HTTPException(422, "INVALID_PREVIEW_FRAME") from error


def create_camera_preview_router(
    cameras: CameraService,
    worker_token: str | None,
    store: PreviewStore | None = None,
    events: object | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Camera preview"])
    store = store or PreviewStore()

    def require(camera_id: str, revision: str) -> None:
        try:
            camera = cameras.require(camera_id)
        except KeyError as error:
            raise HTTPException(404, "CAMERA_NOT_FOUND") from error
        if (
            not camera.enabled
            or camera.lifecycle != "ACTIVE"
            or camera.revision != revision
            or camera.source_type not in {"local_webcam", "rtsp", "webcam"}
        ):
            raise HTTPException(409, "PREVIEW_CAMERA_NOT_ACTIVE")

    @router.post("/internal/cameras/{camera_id}/preview", status_code=204)
    async def push(
        camera_id: str,
        revision: str,
        request: Request,
        x_worker_token: str | None = Header(default=None),
        x_preview_session: str = Header(default="", max_length=128),
        x_person_detections: str = Header(default="[]", max_length=4096),
        x_person_deployment: str = Header(default="", max_length=128),
    ) -> Response:
        if worker_token is None or x_worker_token != worker_token:
            raise HTTPException(403, "worker authorization required")
        require(camera_id, revision)
        try:
            import json

            persons = tuple(PersonDetections(items=json.loads(x_person_detections)).items)
            if persons and not x_person_deployment:
                raise ValueError
        except (ValueError, TypeError, ValidationError) as error:
            raise HTTPException(422, "INVALID_PERSON_DETECTIONS") from error
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > MAX_PREVIEW_BYTES:
                raise HTTPException(413, "PREVIEW_FRAME_TOO_LARGE")
            payload.extend(chunk)
        frame = bytes(payload)
        await run_in_threadpool(validate_preview, frame)
        require(camera_id, revision)
        ended_tracks = store.put(
            camera_id, revision, frame, x_preview_session, persons, x_person_deployment
        )
        mark_absent = getattr(events, "mark_tracks_absent", None)
        if callable(mark_absent):
            mark_absent(camera_id, x_preview_session, ended_tracks)
        return Response(status_code=204)

    @router.get("/cameras/{camera_id}/preview")
    def latest(camera_id: str, revision: str) -> Response:
        require(camera_id, revision)
        frame = store.get(camera_id, revision)
        return Response(
            content=frame,
            status_code=200 if frame else 204,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
