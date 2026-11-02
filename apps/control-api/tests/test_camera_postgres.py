from control_api.camera_postgres import PostgresCameraRepository, initialize_camera_schema
from control_api.cameras import CameraRecord


def test_reader_never_observes_source_midway_through_camera_update():
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    import pytest

    class GatedCursor(Cursor):
        def __init__(self, db):
            super().__init__()
            self.db = db

        def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO cameras"):
                self.db.row = params
                self.db.written.set()
                assert self.db.release.wait(timeout=2)
            elif sql.startswith("INSERT INTO camera_source_configs"):
                self.db.source = params[1]
                self.db.device_index = params[2]
            elif sql.startswith("SELECT"):
                self.rows = [(*self.db.row, self.db.source, self.db.device_index)]

    class GatedConnection(Connection):
        def __init__(self):
            self.written = Event()
            self.release = Event()
            self.row = ()
            self.source = "rtsp"
            self.device_index = None
            self.commits = 0

        def cursor(self):
            return GatedCursor(self)

    db = GatedConnection()
    repo = PostgresCameraRepository(db)
    record = CameraRecord("cam-1", "Webcam", b"", "", "AUTO", True, 5, "ACTIVE", "r2", "webcam")
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(repo.put, record)
        assert db.written.wait(timeout=1)
        reader = pool.submit(repo.get, record.id)
        try:
            with pytest.raises(TimeoutError):
                reader.result(timeout=0.05)
        finally:
            db.release.set()
        writer.result(timeout=2)
        assert reader.result(timeout=2) == record


class Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class Connection:
    def __init__(self, rows=None):
        self.cursor_value = Cursor(rows)
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def test_postgres_repository_persists_secret_separately_and_commits():
    db = Connection()
    repo = PostgresCameraRepository(db)
    record = CameraRecord(
        "cam-1",
        "Entrance",
        b"encrypted",
        "rtsp://***:***@camera/stream",
        "AUTO",
        True,
        5.0,
        "ACTIVE",
        "rev-1",
    )
    repo.put(record)
    sql, params = db.cursor_value.executed[0]
    assert "camera_secrets" not in sql
    assert b"encrypted" in params
    assert "password" not in str(params)
    assert db.commits == 1


def test_postgres_repository_reads_record():
    row = (
        "cam-1",
        "Entrance",
        b"encrypted",
        "rtsp://***:***@camera/stream",
        "AUTO",
        True,
        5.0,
        "ACTIVE",
        "rev-1",
    )
    repo = PostgresCameraRepository(Connection([row]))
    assert repo.get("cam-1") == CameraRecord(*row)


def test_schema_initializer_executes_and_commits() -> None:
    db = Connection()
    initialize_camera_schema(db)
    sql, params = db.cursor_value.executed[0]
    assert "CREATE TABLE IF NOT EXISTS cameras" in sql
    assert params == ()
    assert db.commits == 1
