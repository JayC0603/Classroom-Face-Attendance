"""Administrator class management and teacher-scoped class/session APIs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from control_api.auth_api import principal_from_request
from control_api.class_domain import ClassService


class ClassroomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    course: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=200)
    term: str = Field(min_length=1, max_length=200)


class ClassroomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    course: str | None = Field(default=None, min_length=1, max_length=200)
    section: str | None = Field(default=None, min_length=1, max_length=200)
    term: str | None = Field(default=None, min_length=1, max_length=200)


class TeacherAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_id: str = Field(min_length=1, max_length=200)


class TeacherReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_ids: list[str] = Field(max_length=100)


class MembershipChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str = Field(min_length=1, max_length=200)


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime
    timezone_id: str = Field(min_length=1, max_length=100)


def _service_error(error: ValueError) -> HTTPException:
    code = str(error)
    if code in {"TEACHER_ALREADY_ASSIGNED", "PERSON_ALREADY_ENROLLED"}:
        return HTTPException(409, code)
    return HTTPException(422, code)


def create_class_router(service: ClassService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/admin/classrooms")
    def list_classrooms(request: Request) -> dict[str, object]:
        principal_from_request(request)
        return {"items": service.list_classrooms()}

    @router.post("/admin/classrooms", status_code=status.HTTP_201_CREATED)
    def create_classroom(body: ClassroomCreate, request: Request) -> object:
        principal_from_request(request)
        try:
            return service.create_classroom(body.name, body.course, body.section, body.term)
        except ValueError as error:
            raise _service_error(error) from error

    @router.get("/admin/classrooms/{classroom_id}")
    def get_classroom(classroom_id: str, request: Request) -> object:
        principal_from_request(request)
        try:
            return service.get_classroom(classroom_id)
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error

    @router.patch("/admin/classrooms/{classroom_id}")
    def update_classroom(classroom_id: str, body: ClassroomUpdate, request: Request) -> object:
        principal_from_request(request)
        try:
            return service.update_classroom(classroom_id, **body.model_dump())
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        except ValueError as error:
            raise _service_error(error) from error

    @router.delete("/admin/classrooms/{classroom_id}", status_code=status.HTTP_204_NO_CONTENT)
    def disable_classroom(classroom_id: str, request: Request) -> Response:
        principal_from_request(request)
        try:
            service.disable_classroom(classroom_id)
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/admin/classrooms/{classroom_id}/teachers")
    def list_teachers(classroom_id: str, request: Request) -> dict[str, object]:
        principal_from_request(request)
        try:
            return {"items": service.assigned_teachers(classroom_id)}
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error

    @router.post("/admin/classrooms/{classroom_id}/teachers", status_code=status.HTTP_201_CREATED)
    def add_teacher(classroom_id: str, body: TeacherAssignment, request: Request) -> object:
        principal_from_request(request)
        try:
            service.add_teacher(classroom_id, body.teacher_id)
            return {"teacher_id": body.teacher_id}
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        except ValueError as error:
            raise _service_error(error) from error

    @router.put("/admin/classrooms/{classroom_id}/teachers")
    def replace_teachers(
        classroom_id: str, body: TeacherReplacement, request: Request
    ) -> dict[str, object]:
        principal_from_request(request)
        try:
            return {"items": service.replace_teachers(classroom_id, body.teacher_ids)}
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        except ValueError as error:
            raise _service_error(error) from error

    @router.delete(
        "/admin/classrooms/{classroom_id}/teachers/{teacher_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_teacher(classroom_id: str, teacher_id: str, request: Request) -> Response:
        principal_from_request(request)
        try:
            service.remove_teacher(classroom_id, teacher_id)
        except KeyError as error:
            raise HTTPException(404, "teacher assignment not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/admin/classrooms/{classroom_id}/members")
    def list_members(classroom_id: str, request: Request) -> dict[str, object]:
        principal_from_request(request)
        try:
            return {"items": service.members(classroom_id)}
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error

    @router.post("/admin/classrooms/{classroom_id}/members", status_code=status.HTTP_201_CREATED)
    def enroll_person(classroom_id: str, body: MembershipChange, request: Request) -> object:
        principal_from_request(request)
        try:
            service.enroll_person(classroom_id, body.person_id)
            return {"person_id": body.person_id}
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        except ValueError as error:
            raise _service_error(error) from error

    @router.delete(
        "/admin/classrooms/{classroom_id}/members/{person_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_person(classroom_id: str, person_id: str, request: Request) -> Response:
        principal_from_request(request)
        try:
            service.remove_person(classroom_id, person_id)
        except KeyError as error:
            raise HTTPException(404, "class membership not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/teacher/classes")
    def teacher_classes(request: Request) -> dict[str, object]:
        principal = principal_from_request(request)
        return {"items": service.teacher_classes(principal)}

    @router.get("/teacher/cameras")
    def teacher_cameras(request: Request) -> dict[str, object]:
        principal = principal_from_request(request)
        try:
            return {"items": service.teacher_cameras(principal)}
        except PermissionError as error:
            raise HTTPException(403, "teacher role required") from error

    @router.get("/teacher/classes/{classroom_id}/sessions")
    def sessions(classroom_id: str, request: Request) -> dict[str, object]:
        principal = principal_from_request(request)
        try:
            return {"items": service.sessions(principal, classroom_id)}
        except (KeyError, PermissionError) as error:
            raise HTTPException(404, "classroom not found") from error

    @router.post("/teacher/classes/{classroom_id}/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(classroom_id: str, body: SessionCreate, request: Request) -> object:
        principal = principal_from_request(request)
        try:
            return service.create_session(principal, classroom_id, **body.model_dump())
        except KeyError as error:
            raise HTTPException(404, "classroom not found") from error
        except PermissionError as error:
            raise HTTPException(403, "teacher role required") from error
        except ValueError as error:
            raise _service_error(error) from error

    @router.get("/teacher/classes/{classroom_id}/sessions/{session_id}/attendance")
    def attendance(classroom_id: str, session_id: str, request: Request) -> dict[str, object]:
        principal = principal_from_request(request)
        try:
            return service.attendance(principal, classroom_id, session_id)
        except KeyError as error:
            raise HTTPException(404, "classroom or session not found") from error
        except PermissionError as error:
            raise HTTPException(403, "teacher role required") from error

    @router.post(
        "/teacher/classes/{classroom_id}/sessions/{session_id}/complete",
        status_code=status.HTTP_200_OK,
    )
    def complete_session(classroom_id: str, session_id: str, request: Request) -> object:
        principal = principal_from_request(request)
        try:
            return service.complete_session(principal, classroom_id, session_id)
        except KeyError as error:
            raise HTTPException(404, "classroom or session not found") from error
        except PermissionError as error:
            raise HTTPException(403, "teacher role required") from error

    return router
