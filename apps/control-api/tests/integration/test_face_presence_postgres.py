import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.camera_postgres import PostgresCameraRepository
from control_api.cameras import CameraCreate, CameraService, FernetCameraSecretProtector
from control_api.database_migrations import apply_database_migrations
from control_api.face_presence_policy import FaceEventCandidate
from control_api.recognition_events import RecognitionEventStore
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo


def test_postgres_migration_camera_settings_alert_restart_and_expiry():
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("FR_TEST_POSTGRES_DSN required")
    schema = "fr_face_alert_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-csearch_path={schema}")

    class Storage:
        objects = {}

        def put(self, key, content, content_type):
            self.objects[key] = content

        def get(self, key):
            return self.objects[key]

        def delete(self, key):
            self.objects.pop(key, None)

    storage = Storage()
    store = None
    camera_connection = psycopg.connect(scoped_dsn)
    try:
        apply_database_migrations(camera_connection, Path("infrastructure/database/migrations"))
        apply_database_migrations(camera_connection, Path("infrastructure/database/migrations"))
        cameras = CameraService(
            PostgresCameraRepository(camera_connection), FernetCameraSecretProtector.generate()
        )
        camera = cameras.create(
            CameraCreate(name="Door", source_type="webcam", unknown_alert_frames=8)
        )
        assert cameras.require(camera.id).unknown_alert_frames == 8
        store = RecognitionEventStore(connection=psycopg.connect(scoped_dsn), storage=storage)
        output = BytesIO()
        Image.new("RGB", (100, 100), "blue").save(output, "JPEG")
        candidate = FaceEventCandidate(
            "UNKNOWN_ALERT", "presence-1", 7, "5", (0.1, 0.1, 0.2, 0.2), 5
        )
        arguments = dict(
            scope="camera/revision/session",
            camera_id=camera.id,
            camera_name="Door",
            session_id="session",
            payload=output.getvalue(),
            retention_days=30,
        )
        first = store.face_events.record(candidate, **arguments)
        store.face_events._queue.join()
        assert store.face_events.record(candidate, **arguments)["id"] == first["id"]
        row = store.face_events.history()[0]
        assert row["evidence_status"] == "READY"
        assert store.face_events.evidence_content(row["evidence_key"]) is not None
        store.close()
        store = RecognitionEventStore(connection=psycopg.connect(scoped_dsn), storage=storage)
        assert store.face_events.history()[0]["id"] == first["id"]
        with store._store._lock, store._store.connection.transaction():
            store._store.connection.execute(
                "UPDATE recognition_face_events SET evidence_expires_at='2000-01-01' WHERE id=%s",
                (first["id"],),
            )
        store.face_events.expire()
        assert store.face_events.history()[0]["evidence_status"] == "EXPIRED"
        assert storage.objects == {}
    finally:
        if store is not None:
            store.close()
        camera_connection.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
