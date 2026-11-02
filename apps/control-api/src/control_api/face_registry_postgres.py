"""Registry transactions use independent connections, never shared cursors."""

from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from control_api.face_registry import (
    FaceImagePublic,
    PersonPublic,
    StoredFaceImage,
    StudentIdAlreadyExists,
)

_PERSON_SELECT = """
SELECT p.id, p.full_name, p.student_id,
 p.person_number,
 CASE WHEN p.active THEN 'ACTIVE' ELSE 'DISABLED' END AS status,
 p.created_at, p.updated_at,
 (SELECT count(*) FROM fr_face_images i
  WHERE i.person_id=p.id AND i.storage_key IS NOT NULL) AS image_count
FROM fr_persons p
"""
_SEARCH = """ WHERE strpos(lower(p.full_name), lower(%s)) > 0
 OR strpos(lower(coalesce(p.student_id,'')), lower(%s)) > 0 """


class PostgresFaceRegistryRepository:
    persistence = "postgresql"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        migrations = Path(__file__).resolve().parents[4] / "infrastructure/database/migrations"
        with psycopg.connect(self.dsn) as connection:
            for name in (
                "004_face_registry.sql",
                "009_auth_accounts.sql",
                "012_person_numbers.sql",
            ):
                connection.execute((migrations / name).read_text(encoding="utf-8"))

    def create_person(self, value: PersonPublic) -> PersonPublic:
        try:
            with psycopg.connect(self.dsn) as connection:
                with connection.transaction():
                    row = connection.execute(
                        "SELECT next_number FROM fr_person_number_counter "
                        "WHERE counter_id=1 FOR UPDATE"
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("PERSON_NUMBER_COUNTER_MISSING")
                    person_number = int(row[0])
                    connection.execute(
                        "UPDATE fr_person_number_counter SET next_number=%s WHERE counter_id=1",
                        (person_number + 1,),
                    )
                    connection.execute(
                        "INSERT INTO fr_persons "
                        "(id,full_name,student_id,active,created_at,updated_at,person_number) "
                        "VALUES (%s,%s,%s,true,%s,%s,%s)",
                        (
                            value.id,
                            value.full_name,
                            value.student_id,
                            value.created_at,
                            value.updated_at,
                            person_number,
                        ),
                    )
        except psycopg.errors.UniqueViolation as error:
            raise StudentIdAlreadyExists from error
        return value.model_copy(update={"person_number": person_number})

    def get_person(self, person_id: str) -> PersonPublic | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            row = connection.execute(_PERSON_SELECT + " WHERE p.id=%s", (person_id,)).fetchone()
        return PersonPublic.model_validate(row) if row else None

    def list_persons(self, search: str, limit: int, offset: int) -> tuple[list[PersonPublic], int]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                _PERSON_SELECT
                + _SEARCH
                + " ORDER BY p.person_number ASC LIMIT %s OFFSET %s",
                (search, search, limit, offset),
            ).fetchall()
            count = connection.execute(
                "SELECT count(*) AS total FROM fr_persons p" + _SEARCH,
                (search, search),
            ).fetchone()
        total = int(count["total"]) if count else 0
        return [PersonPublic.model_validate(row) for row in rows], total

    def add_image(self, value: StoredFaceImage) -> None:
        image = value.public
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "INSERT INTO fr_face_images "
                "(id,person_id,evidence_ref,active,storage_key,original_filename,mime_type,"
                "width,height,checksum,source,processing_state,review_state,created_at) "
                "VALUES (%s,%s,NULL,false,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_REVIEW',%s)",
                (
                    image.id,
                    image.person_id,
                    value.storage_key,
                    image.original_filename,
                    image.mime_type,
                    image.width,
                    image.height,
                    value.checksum,
                    image.source,
                    image.processing_state,
                    image.created_at,
                ),
            )
            connection.execute(
                "UPDATE fr_persons SET updated_at=%s WHERE id=%s",
                (image.created_at, image.person_id),
            )

    def list_images(self, person_id: str) -> list[StoredFaceImage]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id,person_id,storage_key,original_filename,mime_type,width,height,"
                "checksum,source,processing_state,review_state,created_at FROM fr_face_images "
                "WHERE person_id=%s AND storage_key IS NOT NULL ORDER BY created_at,id",
                (person_id,),
            ).fetchall()
        return [self._stored_image(row) for row in rows]

    def list_all_images(self) -> list[StoredFaceImage]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id,person_id,storage_key,original_filename,mime_type,width,height,"
                "checksum,source,processing_state,review_state,created_at FROM fr_face_images "
                "WHERE storage_key IS NOT NULL ORDER BY created_at,id"
            ).fetchall()
        return [self._stored_image(row) for row in rows]

    def get_image(self, image_id: str) -> StoredFaceImage | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT id,person_id,storage_key,original_filename,mime_type,width,height,"
                "checksum,source,processing_state,review_state,created_at FROM fr_face_images "
                "WHERE id=%s AND storage_key IS NOT NULL",
                (image_id,),
            ).fetchone()
        return self._stored_image(row) if row else None

    def replace_image_storage_key(self, image_id: str, storage_key: str) -> None:
        with psycopg.connect(self.dsn) as connection:
            result = connection.execute(
                "UPDATE fr_face_images SET storage_key=%s WHERE id=%s AND storage_key IS NOT NULL",
                (storage_key, image_id),
            )
            if result.rowcount != 1:
                raise KeyError("FACE_IMAGE_NOT_FOUND")

    def review_image(
        self,
        image_id: str,
        reviewer_id: str,
        decision: str,
        reviewed_at: datetime,
        teacher_id: str | None = None,
    ) -> FaceImagePublic | None:
        state = "APPROVED" if decision == "APPROVE" else "REJECTED"
        teacher_scope = (
            "AND EXISTS (SELECT 1 FROM class_memberships m "
            "JOIN class_teachers t ON t.classroom_id=m.classroom_id "
            "JOIN classrooms c ON c.id=m.classroom_id "
            "WHERE m.person_id=fr_face_images.person_id AND t.teacher_user_id=%s "
            "AND c.active=true) "
            if teacher_id is not None
            else ""
        )
        parameters: tuple[object, ...] = (state, reviewer_id, reviewed_at, image_id)
        if teacher_id is not None:
            parameters = (*parameters, teacher_id)
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            if teacher_id is not None:
                authorized = connection.execute(
                    "SELECT 1 FROM class_memberships m "
                    "JOIN class_teachers t ON t.classroom_id=m.classroom_id "
                    "JOIN classrooms c ON c.id=m.classroom_id "
                    "JOIN fr_face_images i ON i.person_id=m.person_id "
                    "WHERE i.id=%s AND t.teacher_user_id=%s AND c.active=true "
                    "FOR SHARE OF m,t,c",
                    (image_id, teacher_id),
                ).fetchone()
                if authorized is None:
                    return None
            row = connection.execute(
                "UPDATE fr_face_images SET review_state=%s,reviewed_by=%s,reviewed_at=%s "
                "WHERE id=%s AND review_state='PENDING_REVIEW' "
                "AND storage_key IS NOT NULL AND original_filename IS NOT NULL "
                "AND mime_type IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL "
                "AND checksum IS NOT NULL AND source IS NOT NULL "
                + teacher_scope
                + "RETURNING id,person_id,original_filename,mime_type,width,height,"
                "processing_state,review_state,source,created_at",
                parameters,
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "INSERT INTO fr_face_image_review_audit "
                "(reviewer_id,person_id,image_id,decision,reviewed_at) VALUES(%s,%s,%s,%s,%s)",
                (reviewer_id, row["person_id"], image_id, state, reviewed_at),
            )
        row["content_url"] = (
            f"/api/v1/persons/{row['person_id']}/face-images/{row['id']}/content"
        )
        return FaceImagePublic.model_validate(row)

    def review_audit(self) -> list[dict[str, object]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT reviewer_id,person_id,image_id,decision,reviewed_at "
                "FROM fr_face_image_review_audit ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _stored_image(row: dict[str, object]) -> StoredFaceImage:
        key, checksum = row.pop("storage_key"), row.pop("checksum")
        row["content_url"] = f"/api/v1/persons/{row['person_id']}/face-images/{row['id']}/content"
        return StoredFaceImage(
            public=FaceImagePublic.model_validate(row),
            storage_key=str(key),
            checksum=str(checksum),
        )
