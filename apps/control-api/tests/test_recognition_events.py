import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.browser_stream import BrowserStreamStore
from control_api.camera_preview import PreviewStore
from control_api.cameras import CameraService, FernetCameraSecretProtector, InMemoryCameraRepository
from control_api.recognition_events import RecognitionEventStore
from control_api.simple_recognition_api import create_simple_recognition_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, content: bytes, content_type: str) -> None:
        assert content_type == "image/jpeg"
        self.objects[key] = content

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024):
        content = self.get(key)
        for offset in range(0, len(content), chunk_size):
            yield content[offset : offset + chunk_size]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def health_check(self) -> None:
        return None


@pytest.fixture
def event_store(tmp_path: Path):
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("FR_TEST_POSTGRES_DSN required for isolated PostgreSQL event checks")
    schema = "fr_events_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")
    connection = psycopg.connect(scoped_dsn)
    migration = Path("infrastructure/database/migrations/008_recognition_events.sql")
    storage = MemoryObjectStorage()
    store = None
    try:
        connection.execute(migration.read_text(encoding="utf-8"))
        store = RecognitionEventStore(connection=connection, storage=storage)
        yield store, connection, storage
    finally:
        if store is not None:
            store.close()
        connection.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def record(store: RecognitionEventStore, *, track_id: int = 4, person_id: str = "person-1"):
    return store.record_verified(
        camera_id="camera-1",
        camera_name="Cam 1",
        session_id="session-1",
        track_id=track_id,
        person_id=person_id,
        full_name="Nguyen An",
        student_id="SV001",
        evidence=b"jpeg-evidence",
        retention_days=30,
        attendance_enabled=True,
    )


def test_verified_event_deduplicates_by_camera_session_track_person(event_store) -> None:
    store, _connection, storage = event_store

    first = record(store)
    duplicate = record(store)

    assert first is not None and duplicate is None
    assert len(store.history()) == 1
    assert len(storage.objects) == 1


def test_attendance_is_once_per_person_per_local_day(event_store) -> None:
    store, _connection, _storage = event_store

    record(store, track_id=4)
    record(store, track_id=5)

    rows = store.attendance_rows()
    assert len(rows) == 1
    assert rows[0]["person_id"] == "person-1"


def test_verified_event_query_uses_sql_camera_roster_and_utc_interval_without_evidence_fields(
    tmp_path: Path, monkeypatch
) -> None:
    store = RecognitionEventStore(tmp_path / "projection-events")
    try:
        event = store.record_verified(
            camera_id="camera-1",
            camera_name="Cam 1",
            session_id="session-1",
            track_id=4,
            person_id="person-1",
            full_name="Nguyen An",
            student_id="SV001",
            evidence=b"private-evidence",
            retention_days=30,
            attendance_enabled=True,
        )
        assert event is not None
        with store._store._db() as db:
            db.execute(
                "UPDATE recognition_events SET recognized_at=? WHERE id=?",
                ("2026-10-20T02:30:00+00:00", event["id"]),
            )
            db.execute(
                "INSERT INTO recognition_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "other-camera",
                    "camera-2",
                    "Cam 2",
                    "session-1",
                    4,
                    "person-1",
                    "Nguyen An",
                    "SV001",
                    "2026-10-20T02:30:00+00:00",
                    "2026-10-20",
                    None,
                    None,
                ),
            )

            for event_id, track_id, recognized_at in (
                ("before-range", 5, "2026-10-20T01:59:59+00:00"),
                ("at-end", 6, "2026-10-20T03:00:00+00:00"),
            ):
                db.execute(
                    "INSERT INTO recognition_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        "camera-1",
                        "Cam 1",
                        "session-1",
                        track_id,
                        "person-1",
                        "Nguyen An",
                        "SV001",
                        recognized_at,
                        "2026-10-20",
                        None,
                        None,
                    ),
                )

        statements: list[str] = []
        connect = store._store._connect

        def traced_connect():
            connection = connect()
            connection.set_trace_callback(statements.append)
            return connection

        monkeypatch.setattr(store._store, "_connect", traced_connect)
        rows = store.verified_events(
            camera_id="camera-1",
            start=datetime(2026, 10, 20, 2, tzinfo=UTC),
            end=datetime(2026, 10, 20, 3, tzinfo=UTC),
            person_ids=["person-1"],
        )

        assert len(rows) == 1
        assert rows[0]["id"] == event["id"]
        assert rows[0]["recognized_at"] == datetime(2026, 10, 20, 2, 30, tzinfo=UTC)
        assert "evidence_key" not in rows[0]
        query = next(
            statement for statement in statements if "FROM recognition_events" in statement
        )
        assert "recognized_at >=" in query
        assert "recognized_at <" in query
    finally:
        store.close()


def test_expiry_clears_evidence_object_and_reference(event_store) -> None:
    store, connection, storage = event_store
    event = record(store)
    assert event is not None
    connection.execute(
        "UPDATE recognition_events SET evidence_expires_at=%s WHERE id=%s",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), event["id"]),
    )
    connection.commit()

    store._expire()

    row = connection.execute(
        "SELECT evidence_key FROM recognition_events WHERE id=%s", (event["id"],)
    ).fetchone()
    assert row == (None,)
    assert storage.objects == {}


def test_missing_expired_object_cleanup_is_idempotent(event_store) -> None:
    store, connection, storage = event_store
    event = record(store)
    assert event is not None
    storage.objects.clear()
    connection.execute(
        "UPDATE recognition_events SET evidence_expires_at=%s WHERE id=%s",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), event["id"]),
    )
    connection.commit()

    store._expire()

    assert connection.execute(
        "SELECT evidence_key FROM recognition_events WHERE id=%s", (event["id"],)
    ).fetchone() == (None,)


def test_evidence_upload_failure_creates_no_event_or_attendance(event_store) -> None:
    _store, connection, _storage = event_store

    class FailingStorage(MemoryObjectStorage):
        def put(self, _key: str, _content: bytes, _content_type: str) -> None:
            raise OSError("MinIO unavailable")

    store = RecognitionEventStore(connection=connection, storage=FailingStorage())
    try:
        with pytest.raises(OSError, match="MinIO unavailable"):
            record(store)
        assert connection.execute("SELECT count(*) FROM recognition_events").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM attendance").fetchone() == (0,)
    finally:
        store.close()


def test_legacy_import_is_idempotent_and_keeps_local_source(tmp_path: Path, event_store) -> None:
    _store, connection, storage = event_store
    legacy_root = tmp_path / "legacy"
    (legacy_root / "evidence").mkdir(parents=True)
    local_evidence = legacy_root / "evidence" / "legacy.jpg"
    local_evidence.write_bytes(b"legacy-jpeg")
    legacy_db = sqlite3.connect(legacy_root / "recognition.sqlite3")
    legacy_db.executescript(
        """CREATE TABLE recognition_events(
           id TEXT PRIMARY KEY,camera_id TEXT,camera_name TEXT,session_id TEXT,track_id INTEGER,
           person_id TEXT,full_name TEXT,student_id TEXT,recognized_at TEXT,attendance_date TEXT,
           evidence_key TEXT,evidence_expires_at TEXT);
           CREATE TABLE attendance(
           id TEXT PRIMARY KEY,attendance_date TEXT,person_id TEXT,full_name TEXT,student_id TEXT,
           first_seen_at TEXT,last_seen_at TEXT,exited_at TEXT,camera_id TEXT,camera_name TEXT,
           event_id TEXT);
           INSERT INTO recognition_events VALUES(
           'legacy-event','camera-1','Cam 1','session-1',4,'person-1','Nguyen An','SV001',
           '2026-10-18T12:00:00+00:00','2026-10-18','legacy.jpg','2026-11-17T12:00:00+00:00');
           INSERT INTO attendance VALUES(
           'legacy-attendance','2026-10-18','person-1','Nguyen An','SV001',
           '2026-10-18T12:00:00+00:00','2026-10-18T12:01:00+00:00',NULL,
           'camera-1','Cam 1','legacy-event');"""
    )
    legacy_db.commit()
    legacy_db.close()

    connection.commit()  # Make the fixture schema visible to independent import connections.
    import_dsn = make_conninfo(
        os.environ["FR_TEST_POSTGRES_DSN"],
        options=connection.info.get_parameters().get("options", ""),
    )
    importer = RecognitionEventStore(
        root=legacy_root, connection=psycopg.connect(import_dsn), storage=storage
    )
    importer.close()
    importer = RecognitionEventStore(
        root=legacy_root, connection=psycopg.connect(import_dsn), storage=storage
    )
    importer.close()

    event = connection.execute(
        "SELECT evidence_key,evidence_checksum,evidence_expires_at FROM recognition_events "
        "WHERE id='legacy-event'"
    ).fetchone()
    assert event[0].startswith("legacy-") and event[0].endswith(".jpg")
    assert event[1] is not None
    assert str(event[2]).startswith("2026-11-17")
    assert connection.execute("SELECT count(*) FROM attendance").fetchone() == (1,)
    assert storage.get(event[0]) == b"legacy-jpeg"
    assert local_evidence.read_bytes() == b"legacy-jpeg"


def test_evidence_api_serves_private_bytes_without_object_url(event_store) -> None:
    store, _connection, _storage = event_store
    event = record(store)
    assert event is not None

    class Comparison:
        def status(self, force: bool = False) -> dict[str, object]:
            return {"state": "READY"}

        def compare(self, *_args) -> dict[str, object]:
            return {"state": "NO_FACE", "faces": []}

    app = FastAPI()
    cameras = CameraService(InMemoryCameraRepository(), FernetCameraSecretProtector.generate())
    app.include_router(
        create_simple_recognition_router(
            Comparison(), cameras, PreviewStore(), BrowserStreamStore(), store
        )
    )
    response = TestClient(app).get(f"/api/v1/recognition/evidence/{event['evidence_key']}")

    assert response.status_code == 200
    assert response.content == b"jpeg-evidence"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
