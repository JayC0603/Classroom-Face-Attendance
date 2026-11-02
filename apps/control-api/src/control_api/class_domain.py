"""Class, assignment, session, and attendance projection domain."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from control_api.auth_domain import Principal


@dataclass(frozen=True)
class Classroom:
    id: str
    name: str
    course: str
    section: str
    term: str
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Person:
    id: str
    full_name: str
    student_id: str | None


@dataclass(frozen=True)
class ClassSession:
    id: str
    classroom_id: str
    camera_id: str
    label: str
    starts_at: datetime
    ends_at: datetime
    timezone_id: str
    state: str
    done: bool
    created_by: str
    created_at: datetime


class ClassRepository(Protocol):
    def list_classrooms(self) -> list[Classroom]: ...
    def get_classroom(self, classroom_id: str) -> Classroom | None: ...
    def create_classroom(self, name: str, course: str, section: str, term: str) -> Classroom: ...
    def update_classroom(
        self,
        classroom_id: str,
        name: str | None,
        course: str | None,
        section: str | None,
        term: str | None,
    ) -> Classroom | None: ...
    def disable_classroom(self, classroom_id: str) -> bool: ...
    def assigned_classes(self, teacher_id: str) -> list[Classroom]: ...
    def teacher_class(self, classroom_id: str, teacher_id: str) -> Classroom | None: ...
    def teacher_exists(self, teacher_id: str) -> bool: ...
    def assigned_teachers(self, classroom_id: str) -> list[dict[str, str]]: ...
    def add_teacher(self, classroom_id: str, teacher_id: str) -> None: ...
    def remove_teacher(self, classroom_id: str, teacher_id: str) -> bool: ...
    def replace_teachers(self, classroom_id: str, teacher_ids: Sequence[str]) -> None: ...
    def student_person(self, person_id: str) -> Person | None: ...
    def class_members(self, classroom_id: str, teacher_id: str | None = None) -> list[Person]: ...
    def add_member(self, classroom_id: str, person_id: str) -> None: ...
    def remove_member(self, classroom_id: str, person_id: str) -> bool: ...
    def create_session(self, session: ClassSession) -> ClassSession: ...
    def class_sessions(
        self, classroom_id: str, teacher_id: str | None = None
    ) -> list[ClassSession]: ...
    def get_session(
        self, classroom_id: str, session_id: str, teacher_id: str | None = None
    ) -> ClassSession | None: ...
    def complete_session(
        self, classroom_id: str, session_id: str, teacher_id: str
    ) -> ClassSession | None: ...


class CameraSummary(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def source_type(self) -> str: ...
    @property
    def enabled(self) -> bool: ...


class CameraLookup(Protocol):
    def require(self, camera_id: str) -> object: ...
    def active_list(self) -> Sequence[CameraSummary]: ...


class VerifiedEventQuery(Protocol):
    def verified_events(
        self, *, camera_id: str, start: datetime, end: datetime, person_ids: Sequence[str]
    ) -> list[dict[str, object]]: ...


class ClassService:
    def __init__(
        self,
        repository: ClassRepository,
        cameras: CameraLookup,
        events: VerifiedEventQuery,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.cameras = cameras
        self.events = events
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _text(value: str, field: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError(f"{field.upper()}_REQUIRED")
        if len(result) > 200:
            raise ValueError(f"{field.upper()}_TOO_LONG")
        return result

    def create_classroom(self, name: str, course: str, section: str, term: str) -> Classroom:
        return self.repository.create_classroom(
            self._text(name, "name"),
            self._text(course, "course"),
            self._text(section, "section"),
            self._text(term, "term"),
        )

    def list_classrooms(self) -> list[Classroom]:
        return self.repository.list_classrooms()

    def get_classroom(self, classroom_id: str) -> Classroom:
        classroom = self.repository.get_classroom(classroom_id)
        if classroom is None:
            raise KeyError("CLASSROOM_NOT_FOUND")
        return classroom

    def update_classroom(
        self,
        classroom_id: str,
        *,
        name: str | None,
        course: str | None,
        section: str | None,
        term: str | None,
    ) -> Classroom:
        if self.repository.get_classroom(classroom_id) is None:
            raise KeyError("CLASSROOM_NOT_FOUND")
        fields = {
            "name": self._text(name, "name") if name is not None else None,
            "course": self._text(course, "course") if course is not None else None,
            "section": self._text(section, "section") if section is not None else None,
            "term": self._text(term, "term") if term is not None else None,
        }
        updated = self.repository.update_classroom(classroom_id, **fields)
        if updated is None:
            raise KeyError("CLASSROOM_NOT_FOUND")
        return updated

    def disable_classroom(self, classroom_id: str) -> None:
        if not self.repository.disable_classroom(classroom_id):
            raise KeyError("CLASSROOM_NOT_FOUND")

    def teacher_classes(self, principal: Principal) -> list[Classroom]:
        self._require_role(principal, "TEACHER")
        return self.repository.assigned_classes(principal.user_id)

    def teacher_cameras(self, principal: Principal) -> list[dict[str, str]]:
        self._require_role(principal, "TEACHER")
        cameras = self.cameras.active_list()
        return [
            {
                "id": camera.id,
                "name": camera.name,
                "source_type": camera.source_type,
            }
            for camera in cameras
            if camera.enabled
        ]

    def teacher_class(self, principal: Principal, classroom_id: str) -> Classroom:
        self._require_role(principal, "TEACHER")
        classroom = self.repository.teacher_class(classroom_id, principal.user_id)
        if classroom is None:
            raise KeyError("CLASSROOM_NOT_FOUND")
        return classroom

    @staticmethod
    def _require_role(principal: Principal, role: str) -> None:
        if principal.role != role:
            raise PermissionError("ROLE_REQUIRED")

    def assigned_teachers(self, classroom_id: str) -> list[dict[str, str]]:
        self.get_classroom(classroom_id)
        return self.repository.assigned_teachers(classroom_id)

    def add_teacher(self, classroom_id: str, teacher_id: str) -> None:
        self.get_classroom(classroom_id)
        if not self.repository.teacher_exists(teacher_id):
            raise ValueError("TEACHER_NOT_FOUND")
        self.repository.add_teacher(classroom_id, teacher_id)

    def remove_teacher(self, classroom_id: str, teacher_id: str) -> None:
        self.get_classroom(classroom_id)
        if not self.repository.remove_teacher(classroom_id, teacher_id):
            raise KeyError("TEACHER_ASSIGNMENT_NOT_FOUND")

    def replace_teachers(
        self, classroom_id: str, teacher_ids: Sequence[str]
    ) -> list[dict[str, str]]:
        self.get_classroom(classroom_id)
        unique = list(dict.fromkeys(teacher_ids))
        if len(unique) != len(teacher_ids):
            raise ValueError("DUPLICATE_TEACHER_ID")
        if any(not self.repository.teacher_exists(teacher_id) for teacher_id in unique):
            raise ValueError("TEACHER_NOT_FOUND")
        self.repository.replace_teachers(classroom_id, unique)
        return self.repository.assigned_teachers(classroom_id)

    def members(self, classroom_id: str) -> list[Person]:
        self.get_classroom(classroom_id)
        return self.repository.class_members(classroom_id)

    def enroll_person(self, classroom_id: str, person_id: str) -> None:
        self.get_classroom(classroom_id)
        if self.repository.student_person(person_id) is None:
            raise ValueError("STUDENT_PERSON_NOT_FOUND")
        self.repository.add_member(classroom_id, person_id)

    def remove_person(self, classroom_id: str, person_id: str) -> None:
        self.get_classroom(classroom_id)
        if not self.repository.remove_member(classroom_id, person_id):
            raise KeyError("CLASS_MEMBERSHIP_NOT_FOUND")

    def create_session(
        self,
        principal: Principal,
        classroom_id: str,
        *,
        camera_id: str,
        label: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_id: str,
    ) -> ClassSession:
        self._require_role(principal, "TEACHER")
        classroom = self.teacher_class(principal, classroom_id)
        if not classroom.active:
            raise ValueError("CLASSROOM_INACTIVE")
        try:
            camera = self.cameras.require(camera_id)
        except KeyError as error:
            raise ValueError("CAMERA_NOT_ACTIVE") from error
        if getattr(camera, "lifecycle", None) != "ACTIVE" or not getattr(camera, "enabled", False):
            raise ValueError("CAMERA_NOT_ACTIVE")
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise ValueError("SESSION_TIMEZONE_REQUIRED")
        if ends_at.tzinfo is None or ends_at.utcoffset() is None:
            raise ValueError("SESSION_TIMEZONE_REQUIRED")
        try:
            zone = ZoneInfo(timezone_id)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("INVALID_SESSION_TIMEZONE") from error
        if (
            starts_at.astimezone(zone).utcoffset() != starts_at.utcoffset()
            or ends_at.astimezone(zone).utcoffset() != ends_at.utcoffset()
        ):
            raise ValueError("SESSION_TIMEZONE_MISMATCH")
        start_utc, end_utc = starts_at.astimezone(UTC), ends_at.astimezone(UTC)
        if end_utc <= start_utc:
            raise ValueError("SESSION_END_MUST_FOLLOW_START")
        return self.repository.create_session(
            ClassSession(
                id=str(uuid4()),
                classroom_id=classroom_id,
                camera_id=camera_id,
                label=self._text(label, "label"),
                starts_at=start_utc,
                ends_at=end_utc,
                timezone_id=timezone_id,
                state="SCHEDULED",
                done=False,
                created_by=principal.user_id,
                created_at=self.clock(),
            )
        )

    def sessions(self, principal: Principal, classroom_id: str) -> list[ClassSession]:
        self.teacher_class(principal, classroom_id)
        return self.repository.class_sessions(classroom_id, principal.user_id)

    def complete_session(
        self, principal: Principal, classroom_id: str, session_id: str
    ) -> ClassSession:
        self.teacher_class(principal, classroom_id)
        session = self.repository.complete_session(classroom_id, session_id, principal.user_id)
        if session is None:
            raise KeyError("CLASS_SESSION_NOT_FOUND")
        return session

    def attendance(
        self, principal: Principal, classroom_id: str, session_id: str
    ) -> dict[str, object]:
        self.teacher_class(principal, classroom_id)
        session = self.repository.get_session(classroom_id, session_id, principal.user_id)
        if session is None:
            raise KeyError("CLASS_SESSION_NOT_FOUND")
        roster = self.repository.class_members(classroom_id, principal.user_id)
        person_ids = tuple(person.id for person in roster)
        observed = self.events.verified_events(
            camera_id=session.camera_id,
            start=session.starts_at,
            end=session.ends_at,
            person_ids=person_ids,
        )
        valid_events: list[tuple[datetime, dict[str, object]]] = []
        for event in observed:
            person_id = str(event.get("person_id", ""))
            event_camera = event.get("camera_id")
            recognized_at = event.get("recognized_at")
            if isinstance(recognized_at, str):
                try:
                    recognized_at = datetime.fromisoformat(recognized_at)
                except ValueError:
                    continue
            if not isinstance(recognized_at, datetime) or recognized_at.tzinfo is None:
                continue
            recognized_at = recognized_at.astimezone(UTC)
            if (
                person_id in person_ids
                and event_camera == session.camera_id
                and session.starts_at <= recognized_at < session.ends_at
            ):
                valid_events.append((recognized_at, event))
        first_by_person: dict[str, tuple[datetime, dict[str, object]]] = {}
        for recognized_at, event in sorted(valid_events, key=lambda pair: pair[0]):
            person_id = str(event["person_id"])
            if person_id not in first_by_person:
                first_by_person[person_id] = (recognized_at, event)
        items: list[dict[str, object]] = []
        for person in roster:
            matched = first_by_person.get(person.id)
            items.append(
                {
                    "person_id": person.id,
                    "full_name": person.full_name,
                    "student_id": person.student_id,
                    "status": "PRESENT" if matched else "UNKNOWN_SYSTEM",
                    "recognized_at": matched[0] if matched else None,
                    "camera_id": session.camera_id if matched else None,
                }
            )
        return {
            "classroom_id": classroom_id,
            "session_id": session_id,
            "timezone_id": session.timezone_id,
            "starts_at": session.starts_at,
            "ends_at": session.ends_at,
            "items": items,
        }


class InMemoryClassRepository:
    """Thread-safe repository for focused tests and explicitly injected local use."""

    def __init__(self) -> None:
        self.classrooms: dict[str, Classroom] = {}
        self.teachers: set[str] = set()
        self.people: dict[str, Person] = {}
        self.student_accounts: set[str] = set()
        self.assignments: set[tuple[str, str]] = set()
        self.memberships: set[tuple[str, str]] = set()
        self.sessions: dict[str, ClassSession] = {}
        self._lock = threading.RLock()

    def seed_teacher(self, teacher_id: str) -> None:
        self.teachers.add(teacher_id)

    def seed_student_person(self, person_id: str, full_name: str, student_id: str | None) -> None:
        self.people[person_id] = Person(person_id, full_name, student_id)
        self.student_accounts.add(person_id)

    def list_classrooms(self) -> list[Classroom]:
        return sorted(self.classrooms.values(), key=lambda item: (item.name.casefold(), item.id))

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        return self.classrooms.get(classroom_id)

    def create_classroom(self, name: str, course: str, section: str, term: str) -> Classroom:
        now = datetime.now(UTC)
        record = Classroom(str(uuid4()), name, course, section, term, True, now, now)
        self.classrooms[record.id] = record
        return record

    def update_classroom(
        self,
        classroom_id: str,
        name: str | None,
        course: str | None,
        section: str | None,
        term: str | None,
    ) -> Classroom | None:
        with self._lock:
            old = self.classrooms.get(classroom_id)
            if old is None:
                return None
            updated = Classroom(
                old.id,
                name if name is not None else old.name,
                course if course is not None else old.course,
                section if section is not None else old.section,
                term if term is not None else old.term,
                old.active,
                old.created_at,
                datetime.now(UTC),
            )
            self.classrooms[classroom_id] = updated
            return updated

    def disable_classroom(self, classroom_id: str) -> bool:
        with self._lock:
            old = self.classrooms.get(classroom_id)
            if old is None:
                return False
            self.classrooms[classroom_id] = Classroom(
                old.id,
                old.name,
                old.course,
                old.section,
                old.term,
                False,
                old.created_at,
                datetime.now(UTC),
            )
            return True

    def assigned_classes(self, teacher_id: str) -> list[Classroom]:
        return sorted(
            (
                self.classrooms[class_id]
                for class_id, assigned in self.assignments
                if assigned == teacher_id and self.classrooms[class_id].active
            ),
            key=lambda item: (item.name.casefold(), item.id),
        )

    def teacher_class(self, classroom_id: str, teacher_id: str) -> Classroom | None:
        classroom = self.classrooms.get(classroom_id)
        return (
            classroom
            if classroom and classroom.active and (classroom_id, teacher_id) in self.assignments
            else None
        )

    def teacher_exists(self, teacher_id: str) -> bool:
        return teacher_id in self.teachers

    def assigned_teachers(self, classroom_id: str) -> list[dict[str, str]]:
        return [
            {"teacher_id": teacher}
            for class_id, teacher in sorted(self.assignments)
            if class_id == classroom_id
        ]

    def add_teacher(self, classroom_id: str, teacher_id: str) -> None:
        with self._lock:
            key = (classroom_id, teacher_id)
            if key in self.assignments:
                raise ValueError("TEACHER_ALREADY_ASSIGNED")
            self.assignments.add(key)

    def remove_teacher(self, classroom_id: str, teacher_id: str) -> bool:
        with self._lock:
            key = (classroom_id, teacher_id)
            if key not in self.assignments:
                return False
            self.assignments.remove(key)
            return True

    def replace_teachers(self, classroom_id: str, teacher_ids: Sequence[str]) -> None:
        with self._lock:
            self.assignments = {key for key in self.assignments if key[0] != classroom_id}
            self.assignments.update((classroom_id, teacher_id) for teacher_id in teacher_ids)

    def student_person(self, person_id: str) -> Person | None:
        return self.people.get(person_id) if person_id in self.student_accounts else None

    def class_members(self, classroom_id: str, teacher_id: str | None = None) -> list[Person]:
        if teacher_id is not None and self.teacher_class(classroom_id, teacher_id) is None:
            return []
        return sorted(
            (
                self.people[person]
                for class_id, person in self.memberships
                if class_id == classroom_id
            ),
            key=lambda item: (item.full_name.casefold(), item.id),
        )

    def add_member(self, classroom_id: str, person_id: str) -> None:
        with self._lock:
            key = (classroom_id, person_id)
            if key in self.memberships:
                raise ValueError("PERSON_ALREADY_ENROLLED")
            self.memberships.add(key)

    def remove_member(self, classroom_id: str, person_id: str) -> bool:
        with self._lock:
            key = (classroom_id, person_id)
            if key not in self.memberships:
                return False
            self.memberships.remove(key)
            return True

    def create_session(self, session: ClassSession) -> ClassSession:
        self.sessions[session.id] = session
        return session

    def class_sessions(
        self, classroom_id: str, teacher_id: str | None = None
    ) -> list[ClassSession]:
        if teacher_id and self.teacher_class(classroom_id, teacher_id) is None:
            return []
        return sorted(
            (item for item in self.sessions.values() if item.classroom_id == classroom_id),
            key=lambda item: item.starts_at,
        )

    def get_session(
        self, classroom_id: str, session_id: str, teacher_id: str | None = None
    ) -> ClassSession | None:
        if teacher_id and self.teacher_class(classroom_id, teacher_id) is None:
            return None
        session = self.sessions.get(session_id)
        return session if session and session.classroom_id == classroom_id else None

    def complete_session(
        self, classroom_id: str, session_id: str, teacher_id: str
    ) -> ClassSession | None:
        if self.teacher_class(classroom_id, teacher_id) is None:
            return None
        session = self.sessions.get(session_id)
        if session is None or session.classroom_id != classroom_id:
            return None
        completed = ClassSession(
            id=session.id,
            classroom_id=session.classroom_id,
            camera_id=session.camera_id,
            label=session.label,
            starts_at=session.starts_at,
            ends_at=session.ends_at,
            timezone_id=session.timezone_id,
            state="DONE",
            done=True,
            created_by=session.created_by,
            created_at=session.created_at,
        )
        self.sessions[session_id] = completed
        return completed
