"""PostgreSQL-authoritative enrollment snapshot and FAISS revision orchestration."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from fr_domain.vector_index import (
    EmbeddingCompatibility,
    EnrollmentVector,
    FaceEmbeddingRecord,
    FaceImageRecord,
    IndexRevision,
    IndexStatus,
    PersonRecord,
    VectorCandidate,
)
from vision_worker.vector_store.faiss_store import FaissVectorStore, VectorIndexError

from control_api.camera_postgres import Connection


class EnrollmentIndexRepository(Protocol):
    def put_person(self, value: PersonRecord) -> None: ...
    def put_face_image(self, value: FaceImageRecord) -> None: ...
    def put_embedding(self, value: FaceEmbeddingRecord) -> None: ...
    def list_active_vectors(self) -> list[EnrollmentVector]: ...
    def put_revision(self, value: IndexRevision) -> None: ...
    def get_revision(self, revision_id: str) -> IndexRevision | None: ...
    def list_revisions(self) -> list[IndexRevision]: ...
    def activate_revision(self, revision_id: str, timestamp: str) -> IndexRevision: ...


class InMemoryEnrollmentIndexRepository:
    """Deterministic test repository; never selected as production source of truth."""

    def __init__(self) -> None:
        self.persons: dict[str, PersonRecord] = {}
        self.images: dict[str, FaceImageRecord] = {}
        self.embeddings: dict[str, FaceEmbeddingRecord] = {}
        self.revisions: dict[str, IndexRevision] = {}

    def put_person(self, value: PersonRecord) -> None:
        self.persons[value.id] = value

    def put_face_image(self, value: FaceImageRecord) -> None:
        if value.person_id not in self.persons:
            raise KeyError("PERSON_NOT_FOUND")
        self.images[value.id] = value

    def put_embedding(self, value: FaceEmbeddingRecord) -> None:
        if value.face_image_id not in self.images:
            raise KeyError("FACE_IMAGE_NOT_FOUND")
        self.embeddings[value.id] = value

    def list_active_vectors(self) -> list[EnrollmentVector]:
        result = []
        for value in self.embeddings.values():
            image = self.images[value.face_image_id]
            person = self.persons[image.person_id]
            if value.active and image.active and person.active:
                result.append(
                    EnrollmentVector(value.id, image.id, person.id, value.vector,
                                     value.compatibility)
                )
        return sorted(result, key=lambda item: item.face_embedding_id)

    def put_revision(self, value: IndexRevision) -> None:
        self.revisions[value.id] = value

    def get_revision(self, revision_id: str) -> IndexRevision | None:
        return self.revisions.get(revision_id)

    def list_revisions(self) -> list[IndexRevision]:
        return sorted(self.revisions.values(), key=lambda item: item.created_at, reverse=True)

    def activate_revision(self, revision_id: str, timestamp: str) -> IndexRevision:
        target = self.revisions[revision_id]
        if target.status not in {IndexStatus.BUILDING, IndexStatus.SUPERSEDED}:
            raise VectorIndexError("INVALID_INDEX_STATE")
        for item in self.revisions.values():
            if item.status is IndexStatus.ACTIVE:
                self.revisions[item.id] = replace(
                    item, status=IndexStatus.SUPERSEDED, superseded_at=timestamp
                )
        active = replace(target, status=IndexStatus.ACTIVE, activated_at=timestamp)
        self.revisions[revision_id] = active
        return active


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fr_persons (
 id text PRIMARY KEY, full_name text NOT NULL, student_id text UNIQUE, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_face_images (
 id text PRIMARY KEY, person_id text NOT NULL REFERENCES fr_persons(id),
 evidence_ref text, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_face_embeddings (
 id text PRIMARY KEY, face_image_id text NOT NULL REFERENCES fr_face_images(id),
 vector double precision[] NOT NULL, compatibility jsonb NOT NULL, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_vector_revisions (
 id text PRIMARY KEY, status text NOT NULL, data jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fr_one_active_vector_revision
 ON fr_vector_revisions ((status)) WHERE status = 'ACTIVE';
"""


def initialize_vector_index_schema(connection: Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)
    connection.commit()


def _revision_from_json(data: Any) -> IndexRevision:
    payload = json.loads(data) if isinstance(data, str) else dict(data)
    payload["compatibility"] = EmbeddingCompatibility(**payload["compatibility"])
    payload["status"] = IndexStatus(payload["status"])
    return IndexRevision(**payload)


class PostgresEnrollmentIndexRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def put_person(self, value: PersonRecord) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fr_persons (id,full_name,student_id,active) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE "
                "SET full_name=EXCLUDED.full_name, student_id=EXCLUDED.student_id, "
                "active=EXCLUDED.active",
                (value.id, value.full_name, value.student_id, value.active),
            )
        self._connection.commit()

    def put_face_image(self, value: FaceImageRecord) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fr_face_images (id,person_id,evidence_ref,active) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE "
                "SET evidence_ref=EXCLUDED.evidence_ref, active=EXCLUDED.active",
                (value.id, value.person_id, value.evidence_ref, value.active),
            )
        self._connection.commit()

    def put_embedding(self, value: FaceEmbeddingRecord) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fr_face_embeddings VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET active=EXCLUDED.active",
                (value.id, value.face_image_id, list(value.vector),
                 json.dumps(asdict(value.compatibility)), value.active),
            )
        self._connection.commit()

    def list_active_vectors(self) -> list[EnrollmentVector]:
        # One SQL statement gives a consistent PostgreSQL snapshot for this rebuild.
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT e.id,i.id,p.id,e.vector,e.compatibility "
                "FROM fr_face_embeddings e JOIN fr_face_images i ON i.id=e.face_image_id "
                "JOIN fr_persons p ON p.id=i.person_id "
                "WHERE e.active AND i.active AND p.active ORDER BY e.id"
            )
            rows = cursor.fetchall()
        result = []
        for embedding_id, image_id, person_id, vector, raw_key in rows:
            key = json.loads(raw_key) if isinstance(raw_key, str) else raw_key
            result.append(
                EnrollmentVector(str(embedding_id), str(image_id), str(person_id),
                                 tuple(float(value) for value in vector),
                                 EmbeddingCompatibility(**key))
            )
        return result

    def put_revision(self, value: IndexRevision) -> None:
        payload = json.dumps(asdict(value))
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fr_vector_revisions VALUES (%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,data=EXCLUDED.data",
                (value.id, value.status.value, payload),
            )
        self._connection.commit()

    def get_revision(self, revision_id: str) -> IndexRevision | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT data FROM fr_vector_revisions WHERE id=%s", (revision_id,))
            row = cursor.fetchone()
        return _revision_from_json(row[0]) if row else None

    def list_revisions(self) -> list[IndexRevision]:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT data FROM fr_vector_revisions ORDER BY id")
            rows = cursor.fetchall()
        return sorted(
            (_revision_from_json(row[0]) for row in rows),
            key=lambda item: item.created_at, reverse=True,
        )

    def activate_revision(self, revision_id: str, timestamp: str) -> IndexRevision:
        # Both status transitions commit together; the partial unique index enforces one ACTIVE.
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT data FROM fr_vector_revisions WHERE id=%s FOR UPDATE", (revision_id,)
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(revision_id)
            target = _revision_from_json(row[0])
            if target.status not in {IndexStatus.BUILDING, IndexStatus.SUPERSEDED}:
                raise VectorIndexError("INVALID_INDEX_STATE")
            cursor.execute(
                "SELECT id,data FROM fr_vector_revisions WHERE status='ACTIVE' FOR UPDATE"
            )
            for old_id, raw in cursor.fetchall():
                old = replace(
                    _revision_from_json(raw), status=IndexStatus.SUPERSEDED,
                    superseded_at=timestamp,
                )
                cursor.execute(
                    "UPDATE fr_vector_revisions SET status=%s,data=%s WHERE id=%s",
                    (old.status.value, json.dumps(asdict(old)), old_id),
                )
            active = replace(target, status=IndexStatus.ACTIVE, activated_at=timestamp)
            cursor.execute(
                "UPDATE fr_vector_revisions SET status=%s,data=%s WHERE id=%s",
                (active.status.value, json.dumps(asdict(active)), active.id),
            )
        self._connection.commit()
        return active


class VectorIndexService:
    def __init__(self, repository: EnrollmentIndexRepository, store: FaissVectorStore) -> None:
        self._repository = repository
        self._store = store
        self._lock = RLock()
        self.startup_error: str | None = None

    @classmethod
    def from_postgres(cls, connection: Connection, root: Path) -> VectorIndexService:
        initialize_vector_index_schema(connection)
        service = cls(PostgresEnrollmentIndexRepository(connection), FaissVectorStore(root))
        active = next(
            (item for item in service._repository.list_revisions()
             if item.status is IndexStatus.ACTIVE),
            None,
        )
        if active is not None:
            try:
                service._store.load(active)
            except (VectorIndexError, ImportError) as error:
                service.startup_error = (
                    "FAISS_UNAVAILABLE" if isinstance(error, ImportError) else str(error)
                )
        return service

    def revisions(self) -> list[IndexRevision]:
        return self._repository.list_revisions()

    def status(self) -> dict[str, object]:
        active = self._store.active_revision
        return {
            "state": "ACTIVE" if active is not None else "NOT_READY",
            "active_revision": asdict(active) if active is not None else None,
            "error_category": self.startup_error,
        }

    def rebuild(self, compatibility: EmbeddingCompatibility) -> IndexRevision:
        revision = IndexRevision(
            str(uuid4()), IndexStatus.BUILDING, compatibility, 0, None, None,
            datetime.now(UTC).isoformat(),
        )
        self._repository.put_revision(revision)
        try:
            vectors = [
                item for item in self._repository.list_active_vectors()
                if item.compatibility == compatibility
            ]
            built = self._store.build(revision, vectors)
            self._repository.put_revision(built)
            return self.activate(built.id)
        except Exception as error:
            category = str(error) if isinstance(error, VectorIndexError) else "INDEX_BUILD_FAILED"
            failed = replace(revision, status=IndexStatus.ERROR, error_category=category)
            self._repository.put_revision(failed)
            raise

    def activate(self, revision_id: str) -> IndexRevision:
        with self._lock:
            revision = self._repository.get_revision(revision_id)
            if revision is None:
                raise KeyError(revision_id)
            previous = self._store.active_revision
            self._store.activate(revision)  # verify before touching authoritative status
            try:
                active = self._repository.activate_revision(
                    revision_id, datetime.now(UTC).isoformat()
                )
            except Exception:
                if previous is None:
                    self._store.close()
                else:
                    self._store.activate(previous)
                raise
            self._store.update_active_metadata(active)
            self.startup_error = None
            return active

    def search(
        self, embedding: tuple[float, ...], compatibility: EmbeddingCompatibility, top_k: int
    ) -> list[VectorCandidate]:
        with self._lock:
            return self._store.search(embedding, compatibility, top_k)


def public_revision(revision: IndexRevision) -> dict[str, Any]:
    return asdict(revision)
