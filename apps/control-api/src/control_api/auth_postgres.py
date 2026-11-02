"""PostgreSQL implementation of the authentication repository."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, cast

from control_api.auth_domain import User
from control_api.camera_postgres import connect_postgres


class PostgresAuthRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _run(self, sql: str, params: tuple[object, ...] = (), *, fetch: str = "none") -> Any:
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    if fetch == "one":
                        return cursor.fetchone()
                    if fetch == "all":
                        return cursor.fetchall()
                    return cursor.rowcount
        finally:
            connection.close()

    @staticmethod
    def _user(row: tuple[Any, ...] | None) -> User | None:
        if row is None:
            return None
        return User(row[0], row[1], row[2], row[3], row[4], row[5])

    def user_by_email(self, email: str) -> User | None:
        return self._user(
            self._run(
                "SELECT id,email,password_hash,role,person_id,disabled "
                "FROM auth_users WHERE email=%s",
                (email,),
                fetch="one",
            )
        )

    def user_by_id(self, user_id: str) -> User | None:
        return self._user(
            self._run(
                "SELECT id,email,password_hash,role,person_id,disabled FROM auth_users WHERE id=%s",
                (user_id,),
                fetch="one",
            )
        )

    def create_user(self, email: str, password_hash: str, role: str, person_id: str | None) -> User:
        user_id = secrets.token_hex(16)
        try:
            row = self._run(
                "INSERT INTO auth_users(id,email,password_hash,role,person_id) "
                "VALUES(%s,%s,%s,%s,%s) "
                "RETURNING id,email,password_hash,role,person_id,disabled",
                (user_id, email, password_hash, role, person_id),
                fetch="one",
            )
        except Exception as error:
            # Do not expose driver details (which may contain submitted values) to HTTP callers.
            if getattr(error, "sqlstate", None) == "23505":
                raise ValueError("ACCOUNT_ALREADY_EXISTS") from error
            if getattr(error, "sqlstate", None) == "23503":
                raise ValueError("PERSON_NOT_FOUND") from error
            raise
        user = self._user(row)
        assert user is not None
        return user

    def count_admins(self) -> int:
        row = self._run("SELECT count(*) FROM auth_users WHERE role='ADMIN'", fetch="one")
        return int(row[0])

    def create_first_admin(self, email: str, password_hash: str) -> User:
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('fr-first-admin-bootstrap'))"
                    )
                    cursor.execute("SELECT count(*) FROM auth_users WHERE role='ADMIN'")
                    if int(cursor.fetchone()[0]):
                        raise ValueError("ADMIN_ALREADY_EXISTS")
                    user_id = secrets.token_hex(16)
                    cursor.execute(
                        "INSERT INTO auth_users(id,email,password_hash,role) "
                        "VALUES(%s,%s,%s,'ADMIN') "
                        "RETURNING id,email,password_hash,role,person_id,disabled",
                        (user_id, email, password_hash),
                    )
                    user = self._user(cursor.fetchone())
                    assert user is not None
                    return user
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise ValueError("ACCOUNT_ALREADY_EXISTS") from error
            raise
        finally:
            connection.close()

    def list_users(self) -> list[User]:
        rows = self._run(
            "SELECT id,email,password_hash,role,person_id,disabled FROM auth_users ORDER BY email",
            fetch="all",
        )
        users = [self._user(row) for row in rows]
        return [user for user in users if user is not None]

    def disable_user(self, user_id: str) -> bool:
        return bool(
            self._run(
                "UPDATE auth_users SET disabled=true,updated_at=now() WHERE id=%s", (user_id,)
            )
        )

    def update_password(self, user_id: str, password_hash: str) -> bool:
        return bool(
            self._run(
                "UPDATE auth_users SET password_hash=%s,updated_at=now() WHERE id=%s",
                (password_hash, user_id),
            )
        )

    def update_password_and_revoke_sessions(self, user_id: str, password_hash: str) -> bool:
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))", ("session:" + user_id,)
                    )
                    cursor.execute(
                        "UPDATE auth_users SET password_hash=%s,updated_at=now() WHERE id=%s",
                        (password_hash, user_id),
                    )
                    if not cursor.rowcount:
                        return False
                    cursor.execute(
                        "UPDATE auth_sessions SET revoked_at=now() "
                        "WHERE user_id=%s AND revoked_at IS NULL",
                        (user_id,),
                    )
                    return True
        finally:
            connection.close()

    def issue_session(
        self,
        user_id: str,
        token_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        expected_password_hash: str,
    ) -> None:
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))", ("session:" + user_id,)
                    )
                    cursor.execute(
                        "SELECT password_hash,disabled FROM auth_users WHERE id=%s FOR UPDATE",
                        (user_id,),
                    )
                    user_row = cursor.fetchone()
                    if user_row is None or user_row[1] or user_row[0] != expected_password_hash:
                        raise ValueError("CREDENTIALS_CHANGED")
                    cursor.execute(
                        "UPDATE auth_sessions SET revoked_at=now() "
                        "WHERE user_id=%s AND revoked_at IS NULL",
                        (user_id,),
                    )
                    cursor.execute(
                        "INSERT INTO auth_sessions(token_hash,user_id,csrf_secret_hash,expires_at) "
                        "VALUES(%s,%s,%s,%s)",
                        (token_hash, user_id, csrf_hash, expires_at),
                    )
        finally:
            connection.close()

    def resolve_session(self, token_hash: str, now: datetime) -> tuple[User, str] | None:
        row = self._run(
            "SELECT u.id,u.email,u.password_hash,u.role,u.person_id,u.disabled,s.csrf_secret_hash "
            "FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id "
            "WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>%s",
            (token_hash, now),
            fetch="one",
        )
        if row is None:
            return None
        return User(row[0], row[1], row[2], row[3], row[4], row[5]), row[6]

    def revoke_session(self, token_hash: str) -> None:
        self._run(
            "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL",
            (token_hash,),
        )

    def revoke_user_sessions(self, user_id: str) -> None:
        self._run(
            "UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
            (user_id,),
        )

    def reserve_login_attempt(
        self, digests: tuple[str, ...], now: datetime, window: timedelta, limit: int
    ) -> str | None:
        attempt_id = secrets.token_hex(16)
        connection = cast(Any, connect_postgres(self.dsn))
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM auth_login_attempts WHERE attempted_at < %s",
                        (now - window * 2,),
                    )
                    # Serialize on each keyed digest, avoiding a concurrent threshold race.
                    for digest in sorted(digests):
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (digest,))
                    cursor.execute(
                        "SELECT digest,count(*) FROM auth_login_attempts "
                        "WHERE digest = ANY(%s) AND attempted_at > %s GROUP BY digest",
                        (list(digests), now - window),
                    )
                    if any(int(row[1]) >= limit for row in cursor.fetchall()):
                        return None
                    cursor.executemany(
                        "INSERT INTO auth_login_attempts("
                        "attempt_id,digest,attempted_at,is_failure) "
                        "VALUES(%s,%s,%s,false)",
                        [(attempt_id, digest, now) for digest in digests],
                    )
                    return attempt_id
        finally:
            connection.close()

    def mark_login_attempt_failed(self, attempt_id: str) -> None:
        self._run(
            "UPDATE auth_login_attempts SET is_failure=true WHERE attempt_id=%s", (attempt_id,)
        )

    def release_login_attempt(self, attempt_id: str) -> None:
        self._run("DELETE FROM auth_login_attempts WHERE attempt_id=%s", (attempt_id,))

    def rotate_csrf(self, token_hash: str, csrf_hash: str, now: datetime) -> bool:
        return bool(
            self._run(
                "UPDATE auth_sessions SET csrf_secret_hash=%s "
                "WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>%s",
                (csrf_hash, token_hash, now),
            )
        )

    def audit(self, actor_user_id: str | None, action: str, target_user_id: str | None) -> None:
        self._run(
            "INSERT INTO auth_security_audit(id,actor_user_id,action,target_user_id) "
            "VALUES(%s,%s,%s,%s)",
            (secrets.token_hex(16), actor_user_id, action, target_user_id),
        )
