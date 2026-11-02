"""PostgreSQL persistence for classrooms, memberships, assignments, and sessions."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from control_api.camera_postgres import connect_postgres
from control_api.class_domain import Classroom, ClassSession, Person


class PostgresClassRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    yield cursor
        finally:
            connection.close()

    @staticmethod
    def _row(cursor: Any) -> dict[str, object] | None:
        values = cursor.fetchone()
        if values is None:
            return None
        if isinstance(values, dict):
            return values
        names = [column.name for column in cursor.description]
        return dict(zip(names, values, strict=True))

    @classmethod
    def _rows(cls, cursor: Any) -> list[dict[str, object]]:
        rows = cursor.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], dict):
            return [dict(row) for row in rows]
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in rows]

    @staticmethod
    def _class(row: dict[str, object] | None) -> Classroom | None:
        if row is None:
            return None
        return Classroom(
            id=str(row["id"]),
            name=str(row["name"]),
            course=str(row["course"]),
            section=str(row["section"]),
            term=str(row["term"]),
            active=bool(row["active"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _session(row: dict[str, object] | None) -> ClassSession | None:
        if row is None:
            return None
        return ClassSession(
            id=str(row["id"]),
            classroom_id=str(row["classroom_id"]),
            camera_id=str(row["camera_id"]),
            label=str(row["label"]),
            starts_at=cast(datetime, row["starts_at"]),
            ends_at=cast(datetime, row["ends_at"]),
            timezone_id=str(row["timezone_id"]),
            state=str(row["state"]),
            done=bool(row["done"]),
            created_by=str(row["created_by"]),
            created_at=cast(datetime, row["created_at"]),
        )

    def list_classrooms(self) -> list[Classroom]:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM classrooms ORDER BY name,id")
            rows = self._rows(cursor)
        return [item for row in rows if (item := self._class(row)) is not None]

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM classrooms WHERE id=%s", (classroom_id,))
            row = self._row(cursor)
        return self._class(row)

    def create_classroom(self, name: str, course: str, section: str, term: str) -> Classroom:
        class_id = str(uuid4())
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO classrooms(id,name,course,section,term) VALUES(%s,%s,%s,%s,%s) "
                "RETURNING *",
                (class_id, name, course, section, term),
            )
            row = self._row(cursor)
        result = self._class(row)
        assert result is not None
        return result

    def update_classroom(
        self,
        classroom_id: str,
        name: str | None,
        course: str | None,
        section: str | None,
        term: str | None,
    ) -> Classroom | None:
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE classrooms SET name=coalesce(%s,name),course=coalesce(%s,course),"
                "section=coalesce(%s,section),term=coalesce(%s,term),updated_at=now() "
                "WHERE id=%s RETURNING *",
                (name, course, section, term, classroom_id),
            )
            row = self._row(cursor)
        return self._class(row)

    def disable_classroom(self, classroom_id: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE classrooms SET active=false,updated_at=now() WHERE id=%s",
                (classroom_id,),
            )
            return int(cursor.rowcount) == 1

    def assigned_classes(self, teacher_id: str) -> list[Classroom]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT c.* FROM classrooms c JOIN class_teachers t ON t.classroom_id=c.id "
                "WHERE t.teacher_user_id=%s AND c.active ORDER BY c.name,c.id",
                (teacher_id,),
            )
            rows = self._rows(cursor)
        return [item for row in rows if (item := self._class(row)) is not None]

    def teacher_class(self, classroom_id: str, teacher_id: str) -> Classroom | None:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT c.* FROM classrooms c JOIN class_teachers t ON t.classroom_id=c.id "
                "WHERE c.id=%s AND t.teacher_user_id=%s AND c.active",
                (classroom_id, teacher_id),
            )
            row = self._row(cursor)
        return self._class(row)

    def teacher_exists(self, teacher_id: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM auth_users WHERE id=%s AND role='TEACHER' AND disabled=false",
                (teacher_id,),
            )
            return cursor.fetchone() is not None

    def assigned_teachers(self, classroom_id: str) -> list[dict[str, str]]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT u.id AS teacher_id,u.email FROM class_teachers t "
                "JOIN auth_users u ON u.id=t.teacher_user_id WHERE t.classroom_id=%s "
                "ORDER BY u.email,u.id",
                (classroom_id,),
            )
            return [
                {"teacher_id": str(row["teacher_id"]), "email": str(row["email"])}
                for row in self._rows(cursor)
            ]

    def add_teacher(self, classroom_id: str, teacher_id: str) -> None:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO class_teachers(classroom_id,teacher_user_id) "
                    "SELECT %s,u.id FROM auth_users u JOIN classrooms c ON c.id=%s "
                    "WHERE u.id=%s AND u.role='TEACHER' AND u.disabled=false AND c.active",
                    (classroom_id, classroom_id, teacher_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("TEACHER_NOT_FOUND")
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise ValueError("TEACHER_ALREADY_ASSIGNED") from error
            raise

    def remove_teacher(self, classroom_id: str, teacher_id: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "DELETE FROM class_teachers WHERE classroom_id=%s AND teacher_user_id=%s",
                (classroom_id, teacher_id),
            )
            return int(cursor.rowcount) == 1

    def replace_teachers(self, classroom_id: str, teacher_ids: Sequence[str]) -> None:
        with self._cursor() as cursor:
            cursor.execute("DELETE FROM class_teachers WHERE classroom_id=%s", (classroom_id,))
            for teacher_id in teacher_ids:
                cursor.execute(
                    "INSERT INTO class_teachers(classroom_id,teacher_user_id) "
                    "SELECT %s,id FROM auth_users WHERE id=%s "
                    "AND role='TEACHER' AND disabled=false",
                    (classroom_id, teacher_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("TEACHER_NOT_FOUND")

    def student_person(self, person_id: str) -> Person | None:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT p.id,p.full_name,p.student_id FROM fr_persons p "
                "JOIN auth_users u ON u.person_id=p.id AND u.role='STUDENT' "
                "WHERE p.id=%s AND p.active AND u.disabled=false",
                (person_id,),
            )
            row = self._row(cursor)
        if row is None:
            return None
        student_id = row["student_id"] if isinstance(row["student_id"], str) else None
        return Person(str(row["id"]), str(row["full_name"]), student_id)

    def class_members(self, classroom_id: str, teacher_id: str | None = None) -> list[Person]:
        with self._cursor() as cursor:
            if teacher_id is None:
                cursor.execute(
                    "SELECT p.id,p.full_name,p.student_id FROM class_memberships m "
                    "JOIN fr_persons p ON p.id=m.person_id WHERE m.classroom_id=%s "
                    "ORDER BY p.full_name,p.id",
                    (classroom_id,),
                )
            else:
                cursor.execute(
                    "SELECT p.id,p.full_name,p.student_id FROM class_memberships m "
                    "JOIN fr_persons p ON p.id=m.person_id "
                    "JOIN class_teachers t ON t.classroom_id=m.classroom_id "
                    "JOIN classrooms c ON c.id=m.classroom_id "
                    "WHERE m.classroom_id=%s AND t.teacher_user_id=%s AND c.active "
                    "ORDER BY p.full_name,p.id",
                    (classroom_id, teacher_id),
                )
            rows = self._rows(cursor)
        return [
            Person(
                str(row["id"]),
                str(row["full_name"]),
                row["student_id"] if isinstance(row["student_id"], str) else None,
            )
            for row in rows
        ]

    def add_member(self, classroom_id: str, person_id: str) -> None:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO class_memberships(classroom_id,person_id) "
                    "SELECT c.id,p.id FROM classrooms c CROSS JOIN fr_persons p "
                    "JOIN auth_users u ON u.person_id=p.id AND u.role='STUDENT' "
                    "WHERE c.id=%s AND c.active AND p.id=%s AND p.active AND u.disabled=false",
                    (classroom_id, person_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("STUDENT_PERSON_NOT_FOUND")
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise ValueError("PERSON_ALREADY_ENROLLED") from error
            raise

    def remove_member(self, classroom_id: str, person_id: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "DELETE FROM class_memberships WHERE classroom_id=%s AND person_id=%s",
                (classroom_id, person_id),
            )
            return int(cursor.rowcount) == 1

    def create_session(self, session: ClassSession) -> ClassSession:
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO class_sessions(id,classroom_id,camera_id,label,starts_at,ends_at,"
                "timezone_id,state,done,created_by,created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    session.id,
                    session.classroom_id,
                    session.camera_id,
                    session.label,
                    session.starts_at,
                    session.ends_at,
                    session.timezone_id,
                    session.state,
                    session.done,
                    session.created_by,
                    session.created_at,
                ),
            )
            row = self._row(cursor)
        result = self._session(row)
        assert result is not None
        return result

    def class_sessions(
        self, classroom_id: str, teacher_id: str | None = None
    ) -> list[ClassSession]:
        with self._cursor() as cursor:
            if teacher_id is None:
                cursor.execute(
                    "SELECT * FROM class_sessions WHERE classroom_id=%s ORDER BY starts_at,id",
                    (classroom_id,),
                )
            else:
                cursor.execute(
                    "SELECT s.* FROM class_sessions s JOIN class_teachers t "
                    "ON t.classroom_id=s.classroom_id JOIN classrooms c ON c.id=s.classroom_id "
                    "WHERE s.classroom_id=%s AND c.active "
                    "AND t.teacher_user_id=%s ORDER BY s.starts_at,s.id",
                    (classroom_id, teacher_id),
                )
            rows = self._rows(cursor)
        return [item for row in rows if (item := self._session(row)) is not None]

    def get_session(
        self, classroom_id: str, session_id: str, teacher_id: str | None = None
    ) -> ClassSession | None:
        with self._cursor() as cursor:
            if teacher_id is None:
                cursor.execute(
                    "SELECT * FROM class_sessions WHERE classroom_id=%s AND id=%s",
                    (classroom_id, session_id),
                )
            else:
                cursor.execute(
                    "SELECT s.* FROM class_sessions s JOIN class_teachers t "
                    "ON t.classroom_id=s.classroom_id JOIN classrooms c ON c.id=s.classroom_id "
                    "WHERE s.classroom_id=%s AND s.id=%s AND c.active "
                    "AND t.teacher_user_id=%s",
                    (classroom_id, session_id, teacher_id),
                )
            row = self._row(cursor)
        return self._session(row)

    def complete_session(
        self, classroom_id: str, session_id: str, teacher_id: str
    ) -> ClassSession | None:
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE class_sessions SET state='DONE',done=true "
                "WHERE classroom_id=%s AND id=%s AND state='SCHEDULED' "
                "AND EXISTS (SELECT 1 FROM class_teachers t JOIN classrooms c "
                "ON c.id=t.classroom_id WHERE t.classroom_id=%s "
                "AND t.teacher_user_id=%s AND c.active) RETURNING *",
                (classroom_id, session_id, classroom_id, teacher_id),
            )
            row = self._row(cursor)
        return self._session(row)
