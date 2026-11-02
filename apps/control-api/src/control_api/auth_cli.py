"""Explicit, one-time first administrator bootstrap command."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Any, cast

from control_api.auth_domain import AuthService
from control_api.auth_postgres import PostgresAuthRepository
from control_api.camera_postgres import connect_postgres
from control_api.database_migrations import apply_database_migrations


def main() -> int:
    dsn = os.getenv("FR_POSTGRES_DSN")
    if not dsn:
        print("FR_POSTGRES_DSN must be configured.", file=sys.stderr)
        return 2
    email = input("Administrator email: ").strip()
    password = getpass.getpass("Administrator password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        connection = cast(Any, connect_postgres(dsn))
        try:
            apply_database_migrations(
                connection,
                Path(os.getenv("FR_MIGRATIONS_DIR", "infrastructure/database/migrations")),
            )
        finally:
            connection.close()
        AuthService(PostgresAuthRepository(dsn)).bootstrap_admin(email, password)
    except ValueError as error:
        print("Administrator bootstrap failed: " + str(error), file=sys.stderr)
        return 1
    print("First administrator created. Sign in through the application.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
