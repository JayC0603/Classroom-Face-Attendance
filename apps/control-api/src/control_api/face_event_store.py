"""Durable face-entry / unknown alerts, with bounded asynchronous evidence work."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from PIL import Image

from control_api.face_presence_policy import FaceEventCandidate, event_dedupe_key
from control_api.object_storage import ObjectNotFound

SCHEMA = """
CREATE TABLE IF NOT EXISTS recognition_face_events (
    id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('FACE_CAPTURED','UNKNOWN_ALERT')),
    camera_id TEXT NOT NULL, camera_name TEXT NOT NULL, session_id TEXT NOT NULL,
    track_id BIGINT, frame_id TEXT NOT NULL, recognized_at TEXT NOT NULL,
    consecutive_unknown INTEGER NOT NULL,
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('PENDING','READY','FAILED','EXPIRED')),
    evidence_key TEXT, evidence_error TEXT, evidence_expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recognition_face_events_time_idx
    ON recognition_face_events(recognized_at DESC);
"""


class FaceEventStore:
    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.postgres = hasattr(backend, "connection")
        self._queue: Queue[tuple[str, bytes, tuple[float, float, float, float]]] = Queue(32)
        self._stop = Event()
        if not self.postgres:
            with self._cursor() as cursor:
                cursor.executescript(SCHEMA)
        with self._cursor() as cursor:
            self._execute(
                cursor,
                "UPDATE recognition_face_events SET evidence_status='FAILED',"
                "evidence_error='CAPTURE_INTERRUPTED' WHERE evidence_status='PENDING' "
                "AND recognized_at < ?",
                ((datetime.now(UTC) - timedelta(seconds=30)).isoformat(),),
            )
        self._thread = Thread(target=self._run, name="face-event-evidence", daemon=True)
        self._thread.start()

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self.backend._lock:
            if self.postgres:
                with self.backend.connection.transaction(), self.backend.connection.cursor() as c:
                    yield c
            else:
                with self.backend._db() as c:
                    yield c

    def _execute(self, cursor: Any, sql: str, params: tuple[object, ...] = ()) -> Any:
        return cursor.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def _rows(self, cursor: Any, result: Any) -> list[dict[str, object]]:
        if self.postgres:
            return cast(list[dict[str, object]], self.backend._rows(cursor))
        return [dict(row) for row in result.fetchall()]

    def record(
        self,
        candidate: FaceEventCandidate,
        *,
        scope: str,
        camera_id: str,
        camera_name: str,
        session_id: str,
        payload: bytes,
        retention_days: int,
    ) -> dict[str, object]:
        event_id = str(uuid4())
        now = datetime.now(UTC)
        dedupe = event_dedupe_key(scope, candidate)
        # Accept history first. A failed snapshot must never erase an alert.
        with self._cursor() as cursor:
            result = self._execute(
                cursor,
                "INSERT INTO recognition_face_events "
                "(id,dedupe_key,kind,camera_id,camera_name,session_id,track_id,frame_id,"
                "recognized_at,consecutive_unknown,evidence_status,evidence_expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?) ON CONFLICT(dedupe_key) DO NOTHING",
                (
                    event_id,
                    dedupe,
                    candidate.kind,
                    camera_id,
                    camera_name,
                    session_id,
                    candidate.track_id,
                    candidate.frame_id,
                    now.isoformat(),
                    candidate.consecutive_unknown,
                    (now + timedelta(days=retention_days)).isoformat(),
                ),
            )
            inserted = (cursor if self.postgres else result).rowcount == 1
            result = self._execute(
                cursor, "SELECT * FROM recognition_face_events WHERE dedupe_key=?", (dedupe,)
            )
            row = self._rows(cursor, result)[0]
        if inserted:
            try:
                self._queue.put_nowait((event_id, payload, candidate.box))
            except Full:
                self._failed(event_id, "CAPTURE_QUEUE_FULL")
                row.update(evidence_status="FAILED", evidence_error="CAPTURE_QUEUE_FULL")
        return row

    def _failed(self, event_id: str, code: str) -> None:
        with self._cursor() as cursor:
            self._execute(
                cursor,
                "UPDATE recognition_face_events SET evidence_status='FAILED',evidence_error=? "
                "WHERE id=? AND evidence_status='PENDING'",
                (code, event_id),
            )

    def _save(self, event_id: str, payload: bytes, box: tuple[float, float, float, float]) -> None:
        with Image.open(BytesIO(payload)) as source:
            x, y, w, h = box
            # Box is normalized in the original preview; clamp integer rounding.
            bounds = (
                max(0, int(x * source.width)),
                max(0, int(y * source.height)),
                min(source.width, int((x + w) * source.width + 0.999)),
                min(source.height, int((y + h) * source.height + 0.999)),
            )
            crop = source.convert("RGB").crop(bounds)
            output = BytesIO()
            crop.save(output, "JPEG", quality=90)
        key = f"face-{event_id}.jpg"
        content = output.getvalue()
        try:
            if self.postgres:
                self.backend.storage.put(key, content, "image/jpeg")
            else:
                path = self.backend.evidence / key
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(content)
                temporary.replace(path)
            with self._cursor() as cursor:
                result = self._execute(
                    cursor,
                    "UPDATE recognition_face_events SET evidence_status='READY',evidence_key=? "
                    "WHERE id=? AND evidence_status='PENDING'",
                    (key, event_id),
                )
                accepted = (cursor if self.postgres else result).rowcount == 1
            if not accepted:
                self._delete(key)
        except Exception:
            self._delete(key)
            raise

    def _run(self) -> None:
        next_cleanup = monotonic() + 60
        while not self._stop.is_set() or not self._queue.empty():
            if monotonic() >= next_cleanup:
                try:
                    self.expire()
                except Exception:
                    pass  # Retention retries on the next bounded interval.
                next_cleanup = monotonic() + 60
            try:
                event_id, payload, box = self._queue.get(timeout=0.25)
            except Empty:
                continue
            try:
                self._save(event_id, payload, box)
            except Exception:
                try:
                    self._failed(event_id, "CAPTURE_FAILED")
                except Exception:
                    pass  # Pending jobs are reported interrupted on restart.
            finally:
                self._queue.task_done()

    def _delete(self, key: str) -> None:
        if self.postgres:
            try:
                self.backend.storage.delete(key)
            except ObjectNotFound:
                pass
        else:
            (self.backend.evidence / key).unlink(missing_ok=True)

    def expire(self) -> None:
        with self._cursor() as cursor:
            self._execute(
                cursor,
                "UPDATE recognition_face_events SET evidence_status='FAILED',"
                "evidence_error='CAPTURE_TIMEOUT' WHERE evidence_status='PENDING' "
                "AND recognized_at<?",
                ((datetime.now(UTC) - timedelta(seconds=30)).isoformat(),),
            )
            result = self._execute(
                cursor,
                "SELECT id,evidence_key FROM recognition_face_events WHERE evidence_status='READY' "
                "AND evidence_expires_at<=?",
                (datetime.now(UTC).isoformat(),),
            )
            rows = self._rows(cursor, result)
        for row in rows:
            self._delete(str(row["evidence_key"]))
            with self._cursor() as cursor:
                self._execute(
                    cursor,
                    "UPDATE recognition_face_events SET evidence_key=NULL,"
                    "evidence_status='EXPIRED' WHERE id=?",
                    (row["id"],),
                )

    def history(
        self,
        *,
        camera_id: str | None = None,
        kind: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        self.expire()
        clauses, values = [], []
        for column, value in (("camera_id", camera_id), ("kind", kind)):
            if value:
                clauses.append(column + "=?")
                values.append(value)
        if start:
            clauses.append("recognized_at>=?")
            values.append(start)
        if end:
            clauses.append("recognized_at<?")
            values.append(end)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._cursor() as cursor:
            result = self._execute(
                cursor,
                "SELECT * FROM recognition_face_events"
                + where
                + " ORDER BY recognized_at DESC,id LIMIT ? OFFSET ?",
                (*values, max(1, min(limit, 200)), max(0, offset)),
            )
            return self._rows(cursor, result)

    def evidence_content(self, key: str) -> bytes | None:
        with self._cursor() as cursor:
            result = self._execute(
                cursor,
                "SELECT id FROM recognition_face_events WHERE evidence_key=? "
                "AND evidence_status='READY' AND evidence_expires_at>?",
                (key, datetime.now(UTC).isoformat()),
            )
            if not self._rows(cursor, result):
                return None
        if self.postgres:
            try:
                return cast(bytes, self.backend.storage.get(key))
            except ObjectNotFound:
                return None
        path = self.backend.evidence / key
        return path.read_bytes() if path.is_file() else None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
