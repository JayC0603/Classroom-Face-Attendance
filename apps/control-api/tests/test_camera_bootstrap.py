import pytest
from control_api.camera_bootstrap import build_camera_persistence
from cryptography.fernet import Fernet


class Cursor:
    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.sql = sql

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class Connection:
    def __init__(self) -> None:
        self.cursor_value = Cursor()
        self.commits = 0

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1


def test_postgres_mode_requires_dsn_and_stable_encryption_key() -> None:
    connection = Connection()
    key = Fernet.generate_key().decode()
    persistence = build_camera_persistence(
        {"FR_POSTGRES_DSN": "postgresql://database/face", "FR_CAMERA_ENCRYPTION_KEY": key},
        connector=lambda _dsn: connection,
    )
    assert persistence.mode == "postgresql"
    assert connection.commits == 1


@pytest.mark.parametrize(
    "environment",
    [
        {"FR_POSTGRES_DSN": "postgresql://database/face"},
        {"FR_CAMERA_ENCRYPTION_KEY": Fernet.generate_key().decode()},
    ],
)
def test_partial_postgres_configuration_fails_closed(environment: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="configured together"):
        build_camera_persistence(environment)


def test_unconfigured_local_mode_is_explicitly_volatile() -> None:
    assert build_camera_persistence({}).mode == "in_memory"


def test_explicit_postgres_connection_failure_does_not_fallback_to_memory() -> None:
    def unavailable(_dsn: str) -> Connection:
        raise OSError("database unavailable")

    with pytest.raises(OSError, match="database unavailable"):
        build_camera_persistence(
            {
                "FR_POSTGRES_DSN": "postgresql://db.invalid/face",
                "FR_CAMERA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            },
            connector=unavailable,
        )
