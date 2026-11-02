"""Small, transactional runner for ordered PostgreSQL schema migrations."""

from pathlib import Path


def apply_database_migrations(connection: object, migrations_dir: Path) -> None:
    """Apply each numbered SQL migration once, recording it in PostgreSQL."""
    with connection.transaction():  # type: ignore[attr-defined]
        with connection.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", (61451, 1))
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
            )
            cursor.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}

            for migration in sorted(migrations_dir.glob("[0-9]*.sql")):
                if migration.name in applied:
                    continue
                cursor.execute(migration.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )
                applied.add(migration.name)
