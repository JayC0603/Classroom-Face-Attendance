import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.class_domain import ClassSession
from control_api.class_postgres import PostgresClassRepository
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def class_repository():
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("FR_TEST_POSTGRES_DSN required for isolated PostgreSQL class checks")
    schema = "fr_classes_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(scoped_dsn) as connection:
            for filename in (
                "001_cameras.sql",
                "004_face_registry.sql",
                "008_recognition_events.sql",
                "009_auth_accounts.sql",
                "011_face_enrollment_review.sql",
                "010_classrooms_sessions.sql",
            ):
                migration = Path("infrastructure/database/migrations") / filename
                connection.execute(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO cameras(id,name,protected_rtsp_url,redacted_rtsp_url,codec,"
                "enabled,sampling_fps,lifecycle,revision) VALUES "
                "('cam-1','Camera',%s,'','AUTO',true,5,'ACTIVE','rev-1')",
                (b"",),
            )
            connection.execute(
                "INSERT INTO auth_users(id,email,password_hash,role) VALUES "
                "('teacher-1','teacher@example.test','hash','TEACHER')"
            )
            connection.execute(
                "INSERT INTO fr_persons(id,full_name,student_id,active) "
                "VALUES ('person-1','An Nguyen','SV001',true)"
            )
            connection.execute(
                "INSERT INTO auth_users(id,email,password_hash,role,person_id) "
                "VALUES ('student-1','student@example.test','hash','STUDENT','person-1')"
            )
        yield PostgresClassRepository(scoped_dsn), scoped_dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_class_repository_persists_teacher_student_camera_and_session_constraints(class_repository):
    repository, dsn = class_repository
    classroom = repository.create_classroom("Python", "PRF", "SE1801", "Fall 2026")
    repository.add_teacher(classroom.id, "teacher-1")
    repository.add_member(classroom.id, "person-1")
    assert repository.teacher_class(classroom.id, "teacher-1") == classroom
    assert [person.id for person in repository.class_members(classroom.id)] == ["person-1"]

    with pytest.raises(ValueError, match="TEACHER_ALREADY_ASSIGNED"):
        repository.add_teacher(classroom.id, "teacher-1")
    with pytest.raises(ValueError, match="PERSON_ALREADY_ENROLLED"):
        repository.add_member(classroom.id, "person-1")
    with pytest.raises(ValueError, match="TEACHER_NOT_FOUND"):
        repository.add_teacher(classroom.id, "student-1")

    session = ClassSession(
        id="session-1",
        classroom_id=classroom.id,
        camera_id="cam-1",
        label="Morning",
        starts_at=datetime(2026, 10, 20, 2, tzinfo=UTC),
        ends_at=datetime(2026, 10, 20, 3, tzinfo=UTC),
        timezone_id="Asia/Ho_Chi_Minh",
        state="SCHEDULED",
        done=False,
        created_by="teacher-1",
        created_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    repository.create_session(session)
    assert repository.get_session(classroom.id, session.id, "teacher-1") == session
    assert repository.get_session(classroom.id, session.id, "student-1") is None
    completed = repository.complete_session(classroom.id, session.id, "teacher-1")
    assert completed is not None and completed.done and completed.state == "DONE"
    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO class_sessions(id,classroom_id,camera_id,label,starts_at,ends_at,"
                "timezone_id,created_by) VALUES('bad',%s,'cam-1','Bad',%s,%s,'UTC','teacher-1')",
                (
                    classroom.id,
                    datetime(2026, 10, 20, 3, tzinfo=UTC),
                    datetime(2026, 10, 20, 2, tzinfo=UTC),
                ),
            )


def test_postgres_verified_event_query_filters_camera_roster_and_utc_time(class_repository):
    _repository, dsn = class_repository
    starts = datetime(2026, 10, 20, 2, tzinfo=UTC)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO recognition_events(id,camera_id,camera_name,session_id,person_id,"
            "full_name,recognized_at,attendance_date) VALUES "
            "('in','cam-1','Camera','session-1','person-1','An Nguyen',%s,'2026-10-20'),"
            "('other-camera','cam-2','Other','session-1','person-1','An Nguyen',%s,'2026-10-20'),"
            "('other-person','cam-1','Camera','session-1','other','Other',%s,'2026-10-20')",
            (starts.replace(minute=30), starts.replace(minute=30), starts.replace(minute=30)),
        )

    from control_api.recognition_events import RecognitionEventStore

    class Storage:
        def put(self, *_args):
            raise AssertionError("query must not write evidence")

        def get(self, *_args):
            raise AssertionError("query must not read evidence")

        def iter_bytes(self, *_args):
            return iter(())

        def delete(self, *_args):
            pass

        def health_check(self):
            pass

    connection = psycopg.connect(dsn)
    store = RecognitionEventStore(connection=connection, storage=Storage())
    try:
        result = store.verified_events(
            camera_id="cam-1", start=starts, end=starts.replace(hour=3), person_ids=["person-1"]
        )
        assert [row["id"] for row in result] == ["in"]
        assert "evidence_key" not in result[0]
    finally:
        store.close()
        connection.close()


def test_postgres_face_review_denies_teacher_after_assignment_is_removed(
    class_repository, tmp_path
):
    from io import BytesIO

    from control_api.face_image_storage import FaceImageStorage
    from control_api.face_registry import FaceRegistryService
    from control_api.face_registry_postgres import PostgresFaceRegistryRepository
    from PIL import Image

    repository, dsn = class_repository
    registry = FaceRegistryService(
        PostgresFaceRegistryRepository(dsn), FaceImageStorage(tmp_path / "images")
    )
    classroom = repository.create_classroom("Review", "R", "A", "2026")
    repository.add_teacher(classroom.id, "teacher-1")
    repository.add_member(classroom.id, "person-1")
    out = BytesIO()
    Image.new("RGB", (40, 40), "purple").save(out, format="PNG")
    image = registry.save_image("person-1", "student.png", out.getvalue())

    repository.remove_teacher(classroom.id, "teacher-1")

    with pytest.raises(KeyError, match="FACE_IMAGE_NOT_PENDING"):
        registry.review_image(image.id, "teacher-1", "APPROVE", teacher_id="teacher-1")
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT review_state FROM fr_face_images WHERE id=%s", (image.id,)
        ).fetchone() == ("PENDING_REVIEW",)
        assert connection.execute(
            "SELECT count(*) FROM fr_face_image_review_audit WHERE image_id=%s", (image.id,)
        ).fetchone() == (0,)
