from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from control_api.auth_domain import Principal
from control_api.class_domain import ClassService, InMemoryClassRepository


class Cameras:
    def require(self, camera_id: str):
        if camera_id != "cam-active":
            raise KeyError(camera_id)
        return type("Camera", (), {"id": camera_id, "enabled": True, "lifecycle": "ACTIVE"})()

    def active_list(self):
        return [self.require("cam-active")]


class Events:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.query = None

    def verified_events(self, *, camera_id, start, end, person_ids):
        self.query = (camera_id, start, end, tuple(person_ids))
        return [
            row
            for row in self.rows
            if row["camera_id"] == camera_id
            and start <= row["recognized_at"] < end
            and row["person_id"] in person_ids
        ]


def setup_service(event_rows=()):
    repo = InMemoryClassRepository()
    repo.seed_teacher("teacher-1")
    repo.seed_teacher("teacher-2")
    repo.seed_student_person("person-1", "An Nguyen", "SV001")
    repo.seed_student_person("person-2", "Binh Tran", "SV002")
    events = Events(event_rows)
    return ClassService(repo, Cameras(), events), repo, events


def make_class(service: ClassService, *, teacher_id="teacher-1"):
    classroom = service.create_classroom(
        name="Lập trình Python", course="PRF", section="SE1801", term="Fall 2026"
    )
    service.add_teacher(classroom.id, teacher_id)
    service.enroll_person(classroom.id, "person-1")
    return classroom


def test_admin_can_assign_only_known_teacher_and_student_people() -> None:
    service, _repo, _events = setup_service()
    classroom = service.create_classroom("Python", "PRF", "SE1801", "Fall 2026")

    with pytest.raises(ValueError, match="TEACHER_NOT_FOUND"):
        service.add_teacher(classroom.id, "student-1")
    with pytest.raises(ValueError, match="STUDENT_PERSON_NOT_FOUND"):
        service.enroll_person(classroom.id, "ghost")
    service.add_teacher(classroom.id, "teacher-1")
    service.enroll_person(classroom.id, "person-1")
    with pytest.raises(ValueError, match="TEACHER_ALREADY_ASSIGNED"):
        service.add_teacher(classroom.id, "teacher-1")
    with pytest.raises(ValueError, match="PERSON_ALREADY_ENROLLED"):
        service.enroll_person(classroom.id, "person-1")


def test_teacher_ownership_is_derived_from_principal_and_hides_other_class() -> None:
    service, _repo, _events = setup_service()
    classroom = make_class(service)
    other = service.create_classroom("Other", "DB", "SE1801", "Fall 2026")
    teacher = Principal("teacher-1", "t@example.test", "TEACHER")
    stranger = Principal("teacher-2", "x@example.test", "TEACHER")

    assert [item.id for item in service.teacher_classes(teacher)] == [classroom.id]
    with pytest.raises(KeyError):
        service.teacher_class(stranger, classroom.id)
    with pytest.raises(KeyError):
        service.teacher_class(teacher, other.id)


def test_class_session_requires_aware_valid_zone_and_active_camera() -> None:
    service, _repo, _events = setup_service()
    classroom = make_class(service)
    teacher = Principal("teacher-1", "t@example.test", "TEACHER")

    with pytest.raises(ValueError, match="SESSION_TIMEZONE_REQUIRED"):
        service.create_session(
            teacher,
            classroom.id,
            camera_id="cam-active",
            label="Morning",
            starts_at=datetime(2026, 10, 20, 9),
            ends_at=datetime(2026, 10, 20, 10),
            timezone_id="Asia/Ho_Chi_Minh",
        )
    with pytest.raises(ValueError, match="INVALID_SESSION_TIMEZONE"):
        service.create_session(
            teacher,
            classroom.id,
            camera_id="cam-active",
            label="Morning",
            starts_at=datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            ends_at=datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            timezone_id="Mars/Olympus",
        )
    with pytest.raises(ValueError, match="SESSION_TIMEZONE_MISMATCH"):
        service.create_session(
            teacher,
            classroom.id,
            camera_id="cam-active",
            label="Morning",
            starts_at=datetime(2026, 10, 20, 9, tzinfo=UTC),
            ends_at=datetime(2026, 10, 20, 10, tzinfo=UTC),
            timezone_id="Asia/Ho_Chi_Minh",
        )
    with pytest.raises(ValueError, match="SESSION_END_MUST_FOLLOW_START"):
        service.create_session(
            teacher,
            classroom.id,
            camera_id="cam-active",
            label="Morning",
            starts_at=datetime(2026, 10, 20, 10, tzinfo=UTC),
            ends_at=datetime(2026, 10, 20, 9, tzinfo=UTC),
            timezone_id="UTC",
        )
    with pytest.raises(ValueError, match="CAMERA_NOT_ACTIVE"):
        service.create_session(
            teacher,
            classroom.id,
            camera_id="cam-draft",
            label="Morning",
            starts_at=datetime(2026, 10, 20, 9, tzinfo=UTC),
            ends_at=datetime(2026, 10, 20, 10, tzinfo=UTC),
            timezone_id="UTC",
        )

    session = service.create_session(
        teacher,
        classroom.id,
        camera_id="cam-active",
        label="Morning",
        starts_at=datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        ends_at=datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        timezone_id="Asia/Ho_Chi_Minh",
    )
    assert session.starts_at == datetime(2026, 10, 20, 2, tzinfo=UTC)
    assert session.ends_at == datetime(2026, 10, 20, 3, tzinfo=UTC)
    assert session.timezone_id == "Asia/Ho_Chi_Minh"


def test_attendance_projects_only_roster_camera_and_utc_interval_and_never_infers_absence() -> None:
    present_at = datetime(2026, 10, 20, 2, 30, tzinfo=UTC)
    service, _repo, events = setup_service(
        [
            {
                "id": "ok",
                "camera_id": "cam-active",
                "person_id": "person-1",
                "recognized_at": present_at,
            },
            {
                "id": "wrong-camera",
                "camera_id": "elsewhere",
                "person_id": "person-1",
                "recognized_at": present_at,
            },
            {
                "id": "not-roster",
                "camera_id": "cam-active",
                "person_id": "person-2",
                "recognized_at": present_at,
            },
            {
                "id": "before",
                "camera_id": "cam-active",
                "person_id": "person-1",
                "recognized_at": datetime(2026, 10, 20, 1, 59, tzinfo=UTC),
            },
            {
                "id": "at-end",
                "camera_id": "cam-active",
                "person_id": "person-1",
                "recognized_at": datetime(2026, 10, 20, 3, tzinfo=UTC),
            },
        ]
    )
    classroom = make_class(service)
    teacher = Principal("teacher-1", "t@example.test", "TEACHER")
    session = service.create_session(
        teacher,
        classroom.id,
        camera_id="cam-active",
        label="AM",
        starts_at=datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        ends_at=datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        timezone_id="Asia/Ho_Chi_Minh",
    )

    report = service.attendance(teacher, classroom.id, session.id)

    assert report["items"] == [
        {
            "person_id": "person-1",
            "full_name": "An Nguyen",
            "student_id": "SV001",
            "status": "PRESENT",
            "recognized_at": present_at,
            "camera_id": "cam-active",
        }
    ]
    assert events.query == ("cam-active", session.starts_at, session.ends_at, ("person-1",))
    assert all("evidence" not in key for row in report["items"] for key in row)


def test_no_verified_event_is_unknown_system_not_absent() -> None:
    service, _repo, _events = setup_service()
    classroom = make_class(service)
    teacher = Principal("teacher-1", "t@example.test", "TEACHER")
    session = service.create_session(
        teacher,
        classroom.id,
        camera_id="cam-active",
        label="AM",
        starts_at=datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        ends_at=datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        timezone_id="Asia/Ho_Chi_Minh",
    )

    report = service.attendance(teacher, classroom.id, session.id)

    assert report["items"][0]["status"] == "UNKNOWN_SYSTEM"
    assert report["items"][0]["recognized_at"] is None


def test_assigned_teacher_can_mark_own_session_done():
    service, _repo, _events = setup_service()
    classroom = make_class(service)
    teacher = Principal("teacher-1", "t@example.test", "TEACHER")
    session = service.create_session(
        teacher,
        classroom.id,
        camera_id="cam-active",
        label="AM",
        starts_at=datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        ends_at=datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        timezone_id="Asia/Ho_Chi_Minh",
    )

    completed = service.complete_session(teacher, classroom.id, session.id)

    assert completed.state == "DONE"
    assert completed.done is True
    with pytest.raises(KeyError):
        service.complete_session(
            Principal("teacher-2", "x@example.test", "TEACHER"), classroom.id, session.id
        )
