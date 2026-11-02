"""Self-service enrollment and private review APIs."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from control_api.auth_api import principal_from_request
from control_api.auth_domain import Principal
from control_api.class_domain import ClassService
from control_api.face_image_storage import (
    MAX_FILE_BYTES,
    MAX_UPLOAD_FILES,
    ImageValidationError,
)
from control_api.face_registry import (
    FaceImagePublic,
    FaceRegistryService,
    ImageSource,
    PersonPublic,
)
from control_api.face_upload_limits import FaceUploadRoute
from control_api.recognition_events import RecognitionEventStore


class ReviewBody(BaseModel):
    decision: Literal["APPROVE", "REJECT"]


class StudentEventQuery:
    def __init__(self, events: object) -> None:
        self.events = events

    def attendance(self, person_id: str) -> list[dict[str, object]]:
        rows = self.events.attendance_rows(person_id=person_id)  # type: ignore[attr-defined]
        allowed = (
            "id",
            "attendance_date",
            "first_seen_at",
            "last_seen_at",
            "exited_at",
            "camera_id",
            "camera_name",
        )
        return [{key: row.get(key) for key in allowed} for row in rows]

    def history(self, person_id: str) -> list[dict[str, object]]:
        rows = self.events.history(person_id=person_id)  # type: ignore[attr-defined]
        allowed = ("id", "camera_id", "camera_name", "recognized_at", "attendance_date")
        return [{key: row.get(key) for key in allowed} for row in rows]


def _require_role(request: Request, role: str) -> Principal:
    principal = principal_from_request(request)
    if principal.role != role:
        raise HTTPException(403, "role access denied")
    return principal


def create_student_router(
    registry: FaceRegistryService,
    classes: ClassService,
    events: RecognitionEventStore,
    comparison: object,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", route_class=FaceUploadRoute)
    event_query = StudentEventQuery(events)

    def own_person(request: Request) -> PersonPublic:
        principal = _require_role(request, "STUDENT")
        if not principal.person_id:
            raise HTTPException(409, "STUDENT_PERSON_NOT_LINKED")
        try:
            return registry.require_person(principal.person_id)
        except KeyError as error:
            raise HTTPException(409, "STUDENT_PERSON_NOT_LINKED") from error

    def image_content(image_id: str) -> Response:
        try:
            image = registry.require_image(image_id)
            payload = registry.storage.read(image.storage_key)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND") from None
        return Response(
            payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    def person_is_assigned_member(teacher_id: str, person_id: str) -> bool:
        for classroom in classes.repository.assigned_classes(teacher_id):
            members = classes.repository.class_members(classroom.id, teacher_id)
            if any(member.id == person_id for member in members):
                return True
        return False

    def review(
        image_id: str, decision: str, reviewer_id: str, *, teacher_id: str | None = None
    ) -> FaceImagePublic:
        try:
            result = registry.review_image(
                image_id, reviewer_id, decision, teacher_id=teacher_id
            )
        except KeyError as error:
            raise HTTPException(409, "FACE_IMAGE_NOT_PENDING") from error
        # Force a complete refresh after the committed state transition. If the
        # model is temporarily unavailable, the API still reports the committed
        # review state and recognition remains governed by the backend gallery.
        comparison.status(force=True)  # type: ignore[attr-defined]
        return result

    @router.get("/me/profile")
    def profile(request: Request) -> object:
        person = own_person(request)
        comparison_status = comparison.status()  # type: ignore[attr-defined]
        usable = comparison_status.get("state") == "READY" and any(
            isinstance(item, dict)
            and item.get("person_id") == person.id
            and item.get("state") == "READY"
            for item in comparison_status.get("images", [])
        )
        return person.model_copy(update={"recognition_ready": usable})

    @router.get("/me/face-images")
    def my_images(request: Request) -> dict[str, list[dict[str, object]]]:
        person = own_person(request)
        return {
            "items": [
                item.public.model_dump() | {
                    "content_url": f"/api/v1/me/face-images/{item.public.id}/content"
                }
                for item in registry.repository.list_images(person.id)
            ]
        }

    @router.post("/me/face-images", status_code=201)
    def upload_my_images(
        request: Request,
        files: Annotated[list[UploadFile], File()],
        source: Annotated[ImageSource, Form()] = "MANUAL_UPLOAD",
    ) -> dict[str, list[dict[str, object]]]:
        person = own_person(request)
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(422, "TOO_MANY_FILES")
        results: list[dict[str, object]] = []
        for file in files:
            filename = (file.filename or "image").replace("\\", "/").split("/")[-1]
            filename = "".join(ch for ch in filename if ch.isprintable())[:200] or "image"
            try:
                payload = file.file.read(MAX_FILE_BYTES + 1)
                image = registry.save_image(person.id, filename, payload, source)
                results.append({"filename": filename, "status": "STORED_PENDING", "image": image})
            except ImageValidationError as error:
                results.append(
                    {"filename": filename, "status": "REJECTED", "error_category": str(error)}
                )
            except Exception:
                results.append(
                    {
                        "filename": filename,
                        "status": "FAILED",
                        "error_category": "IMAGE_SAVE_FAILED",
                    }
                )
            finally:
                file.file.close()
        return {"results": results}

    @router.get("/me/face-images/{image_id}/content")
    def my_image_content(image_id: str, request: Request) -> Response:
        person = own_person(request)
        image = registry.repository.get_image(image_id)
        if image is None or image.public.person_id != person.id:
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND")
        return image_content(image_id)

    @router.get("/me/attendance")
    def my_attendance(request: Request) -> dict[str, object]:
        person = own_person(request)
        return {"items": event_query.attendance(person.id)}

    @router.get("/me/events")
    def my_events(request: Request) -> dict[str, object]:
        person = own_person(request)
        return {"items": event_query.history(person.id)}

    @router.get("/me/events/{event_id}/evidence")
    def my_event_evidence(event_id: str, request: Request) -> Response:
        person = own_person(request)
        payload = events.person_event_evidence(person.id, event_id)
        if payload is None:
            raise HTTPException(404, "EVENT_EVIDENCE_NOT_FOUND")
        return Response(
            payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/admin/face-images/pending")
    def admin_pending(request: Request) -> dict[str, object]:
        _require_role(request, "ADMIN")
        items = []
        for image in registry.repository.list_all_images():
            if image.public.review_state != "PENDING_REVIEW":
                continue
            person = registry.repository.get_person(image.public.person_id)
            items.append(
                image.public.model_dump(mode="json")
                | {
                    "person_name": person.full_name if person else None,
                    "student_id": person.student_id if person else None,
                    "person_number": person.person_number if person else None,
                }
            )
        return {
            "items": items
        }

    @router.post("/admin/face-images/{image_id}/review")
    def admin_review(image_id: str, body: ReviewBody, request: Request) -> FaceImagePublic:
        principal = _require_role(request, "ADMIN")
        return review(image_id, body.decision, principal.user_id)

    @router.get("/teacher/face-images/pending")
    def teacher_pending(request: Request) -> dict[str, object]:
        principal = _require_role(request, "TEACHER")
        visible = [
            item.public.model_dump() | {
                "content_url": f"/api/v1/teacher/face-images/{item.public.id}/content"
            }
            for item in registry.repository.list_all_images()
            if item.public.review_state == "PENDING_REVIEW"
            and person_is_assigned_member(principal.user_id, item.public.person_id)
        ]
        return {"items": visible}

    @router.get("/teacher/face-images/{image_id}/content")
    def teacher_image_content(image_id: str, request: Request) -> Response:
        principal = _require_role(request, "TEACHER")
        image = registry.repository.get_image(image_id)
        if (
            image is None
            or image.public.review_state != "PENDING_REVIEW"
            or not person_is_assigned_member(principal.user_id, image.public.person_id)
        ):
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND")
        return image_content(image_id)

    @router.post("/teacher/face-images/{image_id}/review")
    def teacher_review(image_id: str, body: ReviewBody, request: Request) -> FaceImagePublic:
        principal = _require_role(request, "TEACHER")
        image = registry.repository.get_image(image_id)
        if image is None or not person_is_assigned_member(
            principal.user_id, image.public.person_id
        ):
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND")
        result = review(
            image_id, body.decision, principal.user_id, teacher_id=principal.user_id
        )
        return result.model_copy(
            update={"content_url": f"/api/v1/teacher/face-images/{image_id}/content"}
        )

    return router
