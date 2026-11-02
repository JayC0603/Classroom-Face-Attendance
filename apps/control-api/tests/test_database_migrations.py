from pathlib import Path

from control_api.database_migrations import apply_database_migrations


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.result = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.connection.executed.append((sql, params))
        if "SELECT version" in sql:
            self.result = [(version,) for version in self.connection.applied]
        elif "INSERT INTO schema_migrations" in sql:
            self.connection.applied.add(str(params[0]))

    def fetchall(self) -> list[tuple[str]]:
        return self.result

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Transaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class Connection:
    def __init__(self) -> None:
        self.applied: set[str] = set()
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> Cursor:
        return Cursor(self)

    def transaction(self) -> Transaction:
        return Transaction()


def test_apply_database_migrations_runs_in_order_once(tmp_path: Path) -> None:
    (tmp_path / "002_second.sql").write_text("SELECT 2;")
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    connection = Connection()

    apply_database_migrations(connection, tmp_path)
    first_run_count = len(connection.executed)
    apply_database_migrations(connection, tmp_path)

    migration_sql = [sql for sql, _ in connection.executed if sql in {"SELECT 1;", "SELECT 2;"}]
    assert migration_sql == ["SELECT 1;", "SELECT 2;"]
    assert len(connection.executed) == first_run_count + 3
    assert sum("pg_advisory_xact_lock" in sql for sql, _ in connection.executed) == 2
