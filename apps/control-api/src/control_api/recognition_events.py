"""Durable verified-recognition history and once-per-day attendance ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from control_api.face_event_store import FaceEventStore
from control_api.object_storage import ObjectNotFound, ObjectStorage


class _SqliteRecognitionEventStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir(exist_ok=True)
        self.db_path = self.root / "recognition.sqlite3"
        self._lock = RLock()
        self._stop_cleanup = Event()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recognition_events(
                    id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, camera_name TEXT NOT NULL,
                    session_id TEXT NOT NULL, track_id INTEGER, person_id TEXT NOT NULL,
                    full_name TEXT NOT NULL, student_id TEXT, recognized_at TEXT NOT NULL,
                    attendance_date TEXT NOT NULL, evidence_key TEXT, evidence_expires_at TEXT,
                    UNIQUE(camera_id,session_id,track_id,person_id)
                );
                CREATE TABLE IF NOT EXISTS attendance(
                    id TEXT PRIMARY KEY, attendance_date TEXT NOT NULL, person_id TEXT NOT NULL,
                    full_name TEXT NOT NULL, student_id TEXT, first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    exited_at TEXT,
                    camera_id TEXT NOT NULL, camera_name TEXT NOT NULL, event_id TEXT NOT NULL,
                    UNIQUE(attendance_date,person_id)
                );
                CREATE INDEX IF NOT EXISTS recognition_events_camera_verified_time_idx
                    ON recognition_events(camera_id,recognized_at,person_id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(attendance)")}
            if "last_seen_at" not in columns:
                db.execute(
                    "ALTER TABLE attendance ADD COLUMN last_seen_at TEXT NOT NULL DEFAULT ''"
                )
            if "exited_at" not in columns:
                db.execute("ALTER TABLE attendance ADD COLUMN exited_at TEXT")
        self._cleanup_thread = Thread(
            target=self._cleanup_loop, name="evidence-retention", daemon=True
        )
        self._cleanup_thread.start()

    def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.wait(60):
            try:
                self._expire()
            except (OSError, sqlite3.Error):
                continue

    def close(self) -> None:
        self._stop_cleanup.set()
        self._cleanup_thread.join(timeout=1)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_verified(
        self,
        *,
        camera_id: str,
        camera_name: str,
        session_id: str,
        track_id: int | None,
        person_id: str,
        full_name: str,
        student_id: str | None,
        evidence: bytes,
        retention_days: int,
        attendance_enabled: bool,
    ) -> dict[str, object] | None:
        now = datetime.now(UTC)
        local_day = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
        key = f"{uuid4().hex}.jpg"
        evidence_path = self.evidence / key
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_bytes(evidence)
        temporary.replace(evidence_path)
        expires = (now + timedelta(days=retention_days)).isoformat()
        with self._lock, self._db() as db:
            if attendance_enabled:
                db.execute(
                    "INSERT OR IGNORE INTO attendance("
                    "id,attendance_date,person_id,full_name,student_id,first_seen_at,"
                    "last_seen_at,exited_at,camera_id,camera_name,event_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        local_day,
                        person_id,
                        full_name,
                        student_id,
                        now.isoformat(),
                        now.isoformat(),
                        None,
                        camera_id,
                        camera_name,
                        "",
                    ),
                )
                db.execute(
                    "UPDATE attendance SET last_seen_at=?,exited_at=NULL "
                    "WHERE attendance_date=? AND person_id=?",
                    (now.isoformat(), local_day, person_id),
                )
            existing = db.execute(
                "SELECT id FROM recognition_events WHERE camera_id=? AND session_id=? "
                "AND track_id IS ? AND person_id=?",
                (camera_id, session_id, track_id, person_id),
            ).fetchone()
            if existing:
                evidence_path.unlink(missing_ok=True)
                return None
            event_id = str(uuid4())
            db.execute(
                "INSERT INTO recognition_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    camera_id,
                    camera_name,
                    session_id,
                    track_id,
                    person_id,
                    full_name,
                    student_id,
                    now.isoformat(),
                    local_day,
                    key,
                    expires,
                ),
            )
            if attendance_enabled:
                db.execute(
                    "UPDATE attendance SET event_id=? WHERE attendance_date=? "
                    "AND person_id=? AND event_id=''",
                    (event_id, local_day, person_id),
                )
        return {"id": event_id, "evidence_key": key}

    def mark_tracks_absent(self, camera_id: str, session_id: str, track_ids: set[int]) -> None:
        if not track_ids:
            return
        placeholders = ",".join("?" for _ in track_ids)
        now = datetime.now(UTC).isoformat()
        with self._lock, self._db() as db:
            records = db.execute(
                "SELECT DISTINCT person_id,attendance_date FROM recognition_events "
                f"WHERE camera_id=? AND session_id=? AND track_id IN ({placeholders})",
                [camera_id, session_id, *track_ids],
            ).fetchall()
            for record in records:
                db.execute(
                    "UPDATE attendance SET exited_at=?,last_seen_at=? "
                    "WHERE attendance_date=? AND person_id=? AND camera_id=?",
                    (now, now, record["attendance_date"], record["person_id"], camera_id),
                )

    def _expire(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._db() as db:
            rows = db.execute(
                "SELECT id,evidence_key FROM recognition_events "
                "WHERE evidence_key IS NOT NULL AND evidence_expires_at<=?",
                (now,),
            ).fetchall()
            for row in rows:
                if row["evidence_key"] and Path(row["evidence_key"]).name == row["evidence_key"]:
                    (self.evidence / row["evidence_key"]).unlink(missing_ok=True)
                db.execute(
                    "UPDATE recognition_events SET evidence_key=NULL WHERE id=?", (row["id"],)
                )

    def history(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        camera_id: str | None = None,
        person_id: str | None = None,
    ) -> list[dict[str, object]]:
        self._expire()
        clauses = []
        values = []
        for column, value in (
            ("attendance_date>=", start),
            ("attendance_date<=", end),
            ("camera_id=", camera_id),
            ("person_id=", person_id),
        ):
            if value:
                clauses.append(column + "?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,camera_id,camera_name,track_id,person_id,full_name,student_id,"
                    "recognized_at,attendance_date,evidence_key FROM recognition_events"
                    + where
                    + " ORDER BY recognized_at DESC LIMIT 1000",
                    values,
                )
            ]

    def attendance_rows(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        camera_id: str | None = None,
        person_id: str | None = None,
    ) -> list[dict[str, object]]:
        clauses = []
        values = []
        for column, value in (
            ("attendance_date>=", start),
            ("attendance_date<=", end),
            ("camera_id=", camera_id),
            ("person_id=", person_id),
        ):
            if value:
                clauses.append(column + "?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM attendance" + where + " ORDER BY attendance_date DESC,full_name",
                    values,
                )
            ]

    def evidence_path(self, key: str) -> Path | None:
        self._expire()
        if Path(key).name != key or not key.endswith(".jpg"):
            return None
        with self._db() as db:
            found = db.execute(
                "SELECT 1 FROM recognition_events WHERE evidence_key=?", (key,)
            ).fetchone()
        path = self.evidence / key
        return path if found and path.is_file() else None

    def evidence_content(self, key: str) -> bytes | None:
        path = self.evidence_path(key)
        return path.read_bytes() if path is not None else None

    def person_event_evidence(self, person_id: str, event_id: str) -> bytes | None:
        self._expire()
        with self._db() as db:
            row = db.execute(
                "SELECT evidence_key FROM recognition_events WHERE id=? AND person_id=?",
                (event_id, person_id),
            ).fetchone()
        key = row["evidence_key"] if row else None
        return self.evidence_content(str(key)) if key else None

    def event_rows(self, **filters: str | None) -> list[dict[str, object]]:
        return self.history(**filters)

    def verified_events(
        self, *, camera_id: str, start: datetime, end: datetime, person_ids: Sequence[str]
    ) -> list[dict[str, object]]:
        if not person_ids:
            return []
        markers = ",".join("?" for _ in person_ids)
        with self._lock, self._db() as db:
            rows = db.execute(
                "SELECT id,camera_id,person_id,recognized_at FROM recognition_events "
                f"WHERE camera_id=? AND recognized_at >= ? AND recognized_at < ? "
                f"AND person_id IN ({markers})",
                (
                    camera_id,
                    start.astimezone(UTC).isoformat(),
                    end.astimezone(UTC).isoformat(),
                    *person_ids,
                ),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            raw_time = datetime.fromisoformat(str(row["recognized_at"]))
            if raw_time.tzinfo is None or raw_time.utcoffset() is None:
                continue
            recognized_at = raw_time.astimezone(UTC)
            if start <= recognized_at < end:
                result.append(
                    {
                        "id": str(row["id"]),
                        "camera_id": str(row["camera_id"]),
                        "person_id": str(row["person_id"]),
                        "recognized_at": recognized_at,
                    }
                )
        return result


class _PostgresRecognitionEventStore:
    def __init__(self, connection: Any, storage: ObjectStorage) -> None:
        self.connection = connection
        self.storage = storage
        self._lock = RLock()
        self._stop_cleanup = Event()
        self._cleanup_thread = Thread(
            target=self._cleanup_loop, name="evidence-retention", daemon=True
        )
        self._cleanup_thread.start()

    def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.wait(60):
            try:
                self._expire()
            except Exception:
                continue

    def close(self) -> None:
        self._stop_cleanup.set()
        self._cleanup_thread.join(timeout=1)
        self.connection.close()

    @staticmethod
    def _rows(cursor: Any) -> list[dict[str, object]]:
        rows = cursor.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], Mapping):
            return [dict(row) for row in rows]
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in rows]

    @staticmethod
    def _attendance_upsert(
        cursor: Any,
        *,
        day: str,
        now: datetime,
        person_id: str,
        full_name: str,
        student_id: str | None,
        camera_id: str,
        camera_name: str,
        event_id: str,
    ) -> None:
        cursor.execute(
            "INSERT INTO attendance "
            "(id,attendance_date,person_id,full_name,student_id,first_seen_at,last_seen_at,"
            "exited_at,camera_id,camera_name,event_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s) "
            "ON CONFLICT (attendance_date,person_id) DO NOTHING",
            (
                str(uuid4()),
                day,
                person_id,
                full_name,
                student_id,
                now,
                now,
                camera_id,
                camera_name,
                event_id,
            ),
        )
        cursor.execute(
            "UPDATE attendance SET last_seen_at=%s,exited_at=NULL "
            "WHERE attendance_date=%s AND person_id=%s",
            (now, day, person_id),
        )
        if event_id:
            cursor.execute(
                "UPDATE attendance SET event_id=%s WHERE attendance_date=%s "
                "AND person_id=%s AND event_id=''",
                (event_id, day, person_id),
            )

    def record_verified(
        self,
        *,
        camera_id: str,
        camera_name: str,
        session_id: str,
        track_id: int | None,
        person_id: str,
        full_name: str,
        student_id: str | None,
        evidence: bytes,
        retention_days: int,
        attendance_enabled: bool,
    ) -> dict[str, object] | None:
        now = datetime.now(UTC)
        day = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
        expires = now + timedelta(days=retention_days)
        with self._lock:
            with self.connection.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM recognition_events WHERE camera_id=%s AND session_id=%s "
                        "AND track_id IS NOT DISTINCT FROM %s AND person_id=%s",
                        (camera_id, session_id, track_id, person_id),
                    )
                    duplicate = cursor.fetchone()
                    if duplicate:
                        if attendance_enabled:
                            self._attendance_upsert(
                                cursor,
                                day=day,
                                now=now,
                                person_id=person_id,
                                full_name=full_name,
                                student_id=student_id,
                                camera_id=camera_id,
                                camera_name=camera_name,
                                event_id="",
                            )
                        return None

            event_id = str(uuid4())
            key = f"{uuid4().hex}.jpg"
            checksum = sha256(evidence).hexdigest()
            self.storage.put(key, evidence, "image/jpeg")
            try:
                with self.connection.transaction():
                    with self.connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO recognition_events "
                            "(id,camera_id,camera_name,session_id,track_id,person_id,full_name,"
                            "student_id,recognized_at,attendance_date,evidence_key,evidence_checksum,"
                            "evidence_expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                event_id,
                                camera_id,
                                camera_name,
                                session_id,
                                track_id,
                                person_id,
                                full_name,
                                student_id,
                                now,
                                day,
                                key,
                                checksum,
                                expires,
                            ),
                        )
                        if attendance_enabled:
                            self._attendance_upsert(
                                cursor,
                                day=day,
                                now=now,
                                person_id=person_id,
                                full_name=full_name,
                                student_id=student_id,
                                camera_id=camera_id,
                                camera_name=camera_name,
                                event_id=event_id,
                            )
            except Exception:
                try:
                    self.storage.delete(key)
                except Exception:
                    pass
                raise
        return {"id": event_id, "evidence_key": key}

    def mark_tracks_absent(self, camera_id: str, session_id: str, track_ids: set[int]) -> None:
        if not track_ids:
            return
        now = datetime.now(UTC)
        markers = ",".join("%s" for _ in track_ids)
        with self._lock, self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT person_id,attendance_date FROM recognition_events "
                    f"WHERE camera_id=%s AND session_id=%s AND track_id IN ({markers})",
                    (camera_id, session_id, *sorted(track_ids)),
                )
                records = self._rows(cursor)
                for record in records:
                    cursor.execute(
                        "UPDATE attendance SET exited_at=%s,last_seen_at=%s "
                        "WHERE attendance_date=%s AND person_id=%s AND camera_id=%s",
                        (now, now, record["attendance_date"], record["person_id"], camera_id),
                    )

    def _expire(self) -> None:
        now = datetime.now(UTC)
        with self._lock:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,evidence_key FROM recognition_events WHERE evidence_key IS NOT NULL "
                    "AND evidence_expires_at<=%s",
                    (now,),
                )
                rows = self._rows(cursor)
            for row in rows:
                key = row["evidence_key"]
                if not isinstance(key, str):
                    continue
                try:
                    self.storage.delete(key)
                except ObjectNotFound:
                    pass
                except Exception:
                    continue
                with self.connection.transaction():
                    with self.connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE recognition_events SET evidence_key=NULL,"
                            "evidence_checksum=NULL "
                            "WHERE id=%s AND evidence_key=%s",
                            (row["id"], key),
                        )

    @staticmethod
    def _filters(
        start: str | None, end: str | None, camera_id: str | None, person_id: str | None
    ) -> tuple[str, tuple[str, ...]]:
        clauses: list[str] = []
        values: list[str] = []
        for column, value in (
            ("attendance_date >=", start),
            ("attendance_date <=", end),
            ("camera_id =", camera_id),
            ("person_id =", person_id),
        ):
            if value:
                clauses.append(f"{column} %s")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, tuple(values)

    def history(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        camera_id: str | None = None,
        person_id: str | None = None,
    ) -> list[dict[str, object]]:
        self._expire()
        where, values = self._filters(start, end, camera_id, person_id)
        with self._lock:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,camera_id,camera_name,track_id,person_id,full_name,student_id,"
                    "recognized_at,attendance_date,evidence_key FROM recognition_events"
                    + where
                    + " ORDER BY recognized_at DESC LIMIT 1000",
                    values,
                )
                return self._rows(cursor)

    def attendance_rows(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        camera_id: str | None = None,
        person_id: str | None = None,
    ) -> list[dict[str, object]]:
        where, values = self._filters(start, end, camera_id, person_id)
        with self._lock:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,attendance_date,person_id,full_name,student_id,first_seen_at,"
                    "last_seen_at,exited_at,camera_id,camera_name,event_id FROM attendance"
                    + where
                    + " ORDER BY attendance_date DESC,full_name",
                    values,
                )
                return self._rows(cursor)

    def event_rows(self, **filters: str | None) -> list[dict[str, object]]:
        return self.history(**filters)

    def verified_events(
        self, *, camera_id: str, start: datetime, end: datetime, person_ids: Sequence[str]
    ) -> list[dict[str, object]]:
        if not person_ids:
            return []
        with self._lock, self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,camera_id,person_id,recognized_at FROM recognition_events "
                "WHERE camera_id=%s AND recognized_at >= %s AND recognized_at < %s "
                "AND person_id = ANY(%s) ORDER BY recognized_at,id",
                (camera_id, start, end, list(person_ids)),
            )
            rows = self._rows(cursor)
        return rows

    def evidence_content(self, key: str) -> bytes | None:
        self._expire()
        with self._lock:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM recognition_events WHERE evidence_key=%s "
                    "AND (evidence_expires_at IS NULL OR evidence_expires_at>%s)",
                    (key, datetime.now(UTC)),
                )
                if cursor.fetchone() is None:
                    return None
        try:
            return self.storage.get(key)
        except ObjectNotFound:
            return None

    def person_event_evidence(self, person_id: str, event_id: str) -> bytes | None:
        self._expire()
        with self._lock, self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT evidence_key FROM recognition_events "
                "WHERE id=%s AND person_id=%s "
                "AND (evidence_expires_at IS NULL OR evidence_expires_at>%s)",
                (event_id, person_id, datetime.now(UTC)),
            )
            row = cursor.fetchone()
            key = row[0] if row else None
        return self.evidence_content(str(key)) if key else None

    def import_legacy(self, root: Path) -> None:
        db_path = root / "recognition.sqlite3"
        if not db_path.is_file():
            return
        legacy = sqlite3.connect(db_path)
        legacy.row_factory = sqlite3.Row
        try:
            events = legacy.execute("SELECT * FROM recognition_events").fetchall()
            attendance = legacy.execute("SELECT * FROM attendance").fetchall()
        finally:
            legacy.close()
        with self._lock:
            for event in events:
                key: str | None = None
                checksum: str | None = None
                source_key = event["evidence_key"]
                if source_key and Path(source_key).name == source_key:
                    source = root / "evidence" / source_key
                    if source.is_file():
                        payload = source.read_bytes()
                        checksum = sha256(payload).hexdigest()
                        legacy_id = sha256(str(event["id"]).encode("utf-8")).hexdigest()[:32]
                        key = f"legacy-{legacy_id}.jpg"
                        self.storage.put(key, payload, "image/jpeg")
                        if sha256(self.storage.get(key)).hexdigest() != checksum:
                            self.storage.delete(key)
                            raise RuntimeError("LEGACY_EVIDENCE_CHECKSUM_MISMATCH")
                try:
                    with self.connection.transaction():
                        with self.connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO recognition_events "
                                "(id,camera_id,camera_name,session_id,track_id,person_id,full_name,"
                                "student_id,recognized_at,attendance_date,evidence_key,evidence_checksum,"
                                "evidence_expires_at) VALUES "
                                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                "ON CONFLICT (id) DO NOTHING",
                                (
                                    event["id"],
                                    event["camera_id"],
                                    event["camera_name"],
                                    event["session_id"],
                                    event["track_id"],
                                    event["person_id"],
                                    event["full_name"],
                                    event["student_id"],
                                    event["recognized_at"],
                                    event["attendance_date"],
                                    key,
                                    checksum,
                                    event["evidence_expires_at"],
                                ),
                            )
                except Exception:
                    if key:
                        try:
                            self.storage.delete(key)
                        except Exception:
                            pass
                    raise
            with self.connection.transaction():
                with self.connection.cursor() as cursor:
                    for row in attendance:
                        cursor.execute(
                            "INSERT INTO attendance "
                            "(id,attendance_date,person_id,full_name,student_id,first_seen_at,"
                            "last_seen_at,exited_at,camera_id,camera_name,event_id) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (id) DO NOTHING",
                            (
                                row["id"],
                                row["attendance_date"],
                                row["person_id"],
                                row["full_name"],
                                row["student_id"],
                                row["first_seen_at"],
                                row["last_seen_at"],
                                row["exited_at"],
                                row["camera_id"],
                                row["camera_name"],
                                row["event_id"],
                            ),
                        )


class RecognitionEventStore:
    """PostgreSQL/MinIO production store with SQLite local fallback when unconfigured."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        connection: Any | None = None,
        storage: ObjectStorage | None = None,
    ) -> None:
        self._face_events: FaceEventStore | None = None
        self._face_events_lock = RLock()
        if connection is None:
            if storage is not None:
                raise ValueError("a PostgreSQL connection is required with object storage")
            self._store: Any = _SqliteRecognitionEventStore(root or Path("data/recognition-events"))
        else:
            if storage is None:
                raise ValueError("object storage is required with PostgreSQL")
            self._store = _PostgresRecognitionEventStore(connection, storage)
            if root is not None:
                self._store.import_legacy(root)

    @property
    def face_events(self) -> FaceEventStore:
        with self._face_events_lock:
            if self._face_events is None:
                self._face_events = FaceEventStore(self._store)
            return self._face_events

    def close(self) -> None:
        if self._face_events is not None:
            self._face_events.close()
            if self._face_events._thread.is_alive():

                def finish_close() -> None:
                    assert self._face_events is not None
                    self._face_events._thread.join()
                    self._store.close()

                Thread(target=finish_close, name="face-events-drain", daemon=True).start()
                return
        self._store.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)
