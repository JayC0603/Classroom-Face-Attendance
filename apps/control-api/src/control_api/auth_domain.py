"""Authentication primitives for opaque browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str
    person_id: str | None = None


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    role: str
    person_id: str | None
    disabled: bool = False

    @property
    def principal(self) -> Principal:
        return Principal(self.id, self.email, self.role, self.person_id)


@dataclass(frozen=True)
class LoginResult:
    principal: Principal
    session_token: str
    csrf_token: str
    expires_at: datetime


class AuthRepository(Protocol):
    def user_by_email(self, email: str) -> User | None: ...
    def user_by_id(self, user_id: str) -> User | None: ...
    def create_user(
        self, email: str, password_hash: str, role: str, person_id: str | None
    ) -> User: ...
    def count_admins(self) -> int: ...
    def create_first_admin(self, email: str, password_hash: str) -> User: ...
    def list_users(self) -> list[User]: ...
    def disable_user(self, user_id: str) -> bool: ...
    def update_password(self, user_id: str, password_hash: str) -> bool: ...
    def update_password_and_revoke_sessions(self, user_id: str, password_hash: str) -> bool: ...
    def issue_session(
        self,
        user_id: str,
        token_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        expected_password_hash: str,
    ) -> None: ...
    def resolve_session(self, token_hash: str, now: datetime) -> tuple[User, str] | None: ...
    def revoke_session(self, token_hash: str) -> None: ...
    def revoke_user_sessions(self, user_id: str) -> None: ...
    def reserve_login_attempt(
        self, digests: tuple[str, ...], now: datetime, window: timedelta, limit: int
    ) -> str | None: ...
    def mark_login_attempt_failed(self, attempt_id: str) -> None: ...
    def release_login_attempt(self, attempt_id: str) -> None: ...
    def rotate_csrf(self, token_hash: str, csrf_hash: str, now: datetime) -> bool: ...


class PasswordHasher:
    """Versioned scrypt encoding; parameters are embedded for future upgrades."""

    N = 2**14
    R = 8
    P = 1
    DKLEN = 64

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode(), salt=salt, n=self.N, r=self.R, p=self.P, dklen=self.DKLEN
        )
        return f"scrypt$v1${self.N}${self.R}${self.P}${salt.hex()}${digest.hex()}"

    def verify(self, password: str, encoded: str) -> bool:
        try:
            algorithm, version, n, r, p, salt_hex, digest_hex = encoded.split("$")
            if algorithm != "scrypt" or version != "v1":
                return False
            expected = bytes.fromhex(digest_hex)
            candidate = hashlib.scrypt(
                password.encode(),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
            return hmac.compare_digest(candidate, expected)
        except (ValueError, TypeError, OverflowError):
            return False


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(hours=12),
        max_login_attempts: int = 10,
        login_window: timedelta = timedelta(minutes=10),
        rate_limit_key: bytes | None = None,
    ) -> None:
        self.repository = repository
        self.hasher = PasswordHasher()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.session_ttl = session_ttl
        self.max_login_attempts = max_login_attempts
        self.login_window = login_window
        configured_key = os.getenv("FR_AUTH_RATE_LIMIT_KEY", "").encode()
        self._rate_key = rate_limit_key or configured_key or secrets.token_bytes(32)
        self._dummy_hash_value = self.hasher.hash(secrets.token_urlsafe(32))

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().casefold()

    def _rate_digests(self, email: str, remote: str) -> tuple[str, ...]:
        result = []
        for value in ("email:" + self.normalize_email(email), "ip:" + remote):
            result.append(hmac.new(self._rate_key, value.encode(), hashlib.sha256).hexdigest())
        return tuple(result)

    def bootstrap_admin(self, email: str, password: str) -> User:
        self._validate_password(password)
        normalized = self.normalize_email(email)
        if "@" not in normalized or any(char.isspace() for char in normalized):
            raise ValueError("INVALID_EMAIL")
        return self.repository.create_first_admin(normalized, self.hasher.hash(password))

    def create_account(
        self, email: str, password: str, role: str, person_id: str | None = None
    ) -> User:
        self._validate_password(password)
        normalized = self.normalize_email(email)
        if not normalized or "@" not in normalized:
            raise ValueError("INVALID_EMAIL")
        if role not in {"ADMIN", "TEACHER", "STUDENT"}:
            raise ValueError("INVALID_ROLE")
        if role == "STUDENT" and not person_id:
            raise ValueError("STUDENT_PERSON_REQUIRED")
        if role != "STUDENT" and person_id is not None:
            raise ValueError("PERSON_ID_ONLY_FOR_STUDENT")
        return self.repository.create_user(normalized, self.hasher.hash(password), role, person_id)

    def login(self, email: str, password: str, remote_address: str) -> LoginResult:
        now = self.clock()
        digests = self._rate_digests(email, remote_address)
        attempt_id = self.repository.reserve_login_attempt(
            digests, now, self.login_window, self.max_login_attempts
        )
        if attempt_id is None:
            raise ValueError("LOGIN_RATE_LIMITED")
        user = self.repository.user_by_email(self.normalize_email(email))
        # Always perform a scrypt operation to reduce account enumeration timing differences.
        valid = (
            self.hasher.verify(password, user.password_hash)
            if user
            else self.hasher.verify(password, self._dummy_hash_value)
        )
        if user is None or user.disabled or not valid:
            self.repository.mark_login_attempt_failed(attempt_id)
            raise ValueError("INVALID_CREDENTIALS")
        try:
            result = self._issue(user, user.password_hash)
        except ValueError as error:
            self.repository.mark_login_attempt_failed(attempt_id)
            if str(error) == "CREDENTIALS_CHANGED":
                raise ValueError("INVALID_CREDENTIALS") from error
            raise
        self.repository.release_login_attempt(attempt_id)
        return result

    def _issue(self, user: User, expected_password_hash: str) -> LoginResult:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = self.clock() + self.session_ttl
        self.repository.issue_session(
            user.id,
            self._secret_hash(token),
            self._secret_hash(csrf),
            expires,
            expected_password_hash,
        )
        return LoginResult(user.principal, token, csrf, expires)

    @staticmethod
    def _secret_hash(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def resolve_session(self, token: str | None) -> Principal | None:
        if not token:
            return None
        result = self.repository.resolve_session(self._secret_hash(token), self.clock())
        if result is None:
            return None
        user, _csrf_hash = result
        return None if user.disabled else user.principal

    def verify_csrf(self, token: str | None, session_token: str | None) -> bool:
        if not token or not session_token:
            return False
        result = self.repository.resolve_session(self._secret_hash(session_token), self.clock())
        return bool(result and hmac.compare_digest(result[1], self._secret_hash(token)))

    def current(self, session_token: str | None) -> tuple[Principal, str] | None:
        if not session_token:
            return None
        principal = self.resolve_session(session_token)
        if principal is None:
            return None
        csrf_token = secrets.token_urlsafe(32)
        if not self.repository.rotate_csrf(
            self._secret_hash(session_token), self._secret_hash(csrf_token), self.clock()
        ):
            return None
        return principal, csrf_token

    def logout(self, token: str) -> None:
        self.repository.revoke_session(self._secret_hash(token))

    def change_password(
        self, principal: Principal, token: str, old_password: str, new_password: str
    ) -> LoginResult:
        user = self.repository.user_by_id(principal.user_id)
        if (
            user is None
            or user.disabled
            or not self.hasher.verify(old_password, user.password_hash)
        ):
            raise ValueError("INVALID_CREDENTIALS")
        self._validate_password(new_password)
        self.repository.update_password_and_revoke_sessions(user.id, self.hasher.hash(new_password))
        updated_user = self.repository.user_by_id(user.id)
        if updated_user is None:
            raise ValueError("INVALID_CREDENTIALS")
        return self._issue(updated_user, updated_user.password_hash)

    def list_accounts(self) -> list[User]:
        return self.repository.list_users()

    def disable_account(self, user_id: str) -> bool:
        disabled = self.repository.disable_user(user_id)
        if disabled:
            self.repository.revoke_user_sessions(user_id)
        return disabled

    def reset_password(self, user_id: str, password: str) -> bool:
        self._validate_password(password)
        return self.repository.update_password_and_revoke_sessions(
            user_id, self.hasher.hash(password)
        )

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12 or len(password) > 1024:
            raise ValueError("PASSWORD_POLICY")


class InMemoryAuthRepository:
    """Explicit test repository; production wiring always uses PostgreSQL."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.sessions: dict[str, tuple[str, str, datetime, bool]] = {}
        self.attempts: dict[str, tuple[tuple[str, ...], datetime, bool]] = {}
        self._lock = threading.RLock()

    def user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    def user_by_id(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    def create_user(self, email: str, password_hash: str, role: str, person_id: str | None) -> User:
        with self._lock:
            if self.user_by_email(email):
                raise ValueError("ACCOUNT_ALREADY_EXISTS")
            user = User(secrets.token_hex(16), email, password_hash, role, person_id)
            self.users[user.id] = user
            return user

    def count_admins(self) -> int:
        return sum(user.role == "ADMIN" for user in self.users.values())

    def create_first_admin(self, email: str, password_hash: str) -> User:
        with self._lock:
            if self.count_admins():
                raise ValueError("ADMIN_ALREADY_EXISTS")
            return self.create_user(email, password_hash, "ADMIN", None)

    def list_users(self) -> list[User]:
        return sorted(self.users.values(), key=lambda item: item.email)

    def disable_user(self, user_id: str) -> bool:
        with self._lock:
            user = self.users.get(user_id)
            if not user:
                return False
            self.users[user_id] = User(**{**user.__dict__, "disabled": True})
            return True

    def update_password(self, user_id: str, password_hash: str) -> bool:
        user = self.users.get(user_id)
        if not user:
            return False
        self.users[user_id] = User(**{**user.__dict__, "password_hash": password_hash})
        return True

    def update_password_and_revoke_sessions(self, user_id: str, password_hash: str) -> bool:
        with self._lock:
            changed = self.update_password(user_id, password_hash)
            if changed:
                self.revoke_user_sessions(user_id)
            return changed

    def issue_session(
        self,
        user_id: str,
        token_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        expected_password_hash: str,
    ) -> None:
        with self._lock:
            user = self.user_by_id(user_id)
            if user is None or user.disabled or user.password_hash != expected_password_hash:
                raise ValueError("CREDENTIALS_CHANGED")
            self.revoke_user_sessions(user_id)
            self.sessions[token_hash] = (user_id, csrf_hash, expires_at, False)

    def resolve_session(self, token_hash: str, now: datetime) -> tuple[User, str] | None:
        record = self.sessions.get(token_hash)
        if record is None or record[3] or record[2] <= now:
            return None
        user = self.users.get(record[0])
        return (user, record[1]) if user else None

    def revoke_session(self, token_hash: str) -> None:
        row = self.sessions.get(token_hash)
        if row:
            self.sessions[token_hash] = (*row[:3], True)

    def revoke_user_sessions(self, user_id: str) -> None:
        for key, row in tuple(self.sessions.items()):
            if row[0] == user_id:
                self.sessions[key] = (*row[:3], True)

    def reserve_login_attempt(
        self, digests: tuple[str, ...], now: datetime, window: timedelta, limit: int
    ) -> str | None:
        with self._lock:
            for digest in digests:
                recent = sum(
                    digest in row_digests and attempted_at > now - window
                    for row_digests, attempted_at, _failed in self.attempts.values()
                )
                if recent >= limit:
                    return None
            attempt_id = secrets.token_hex(16)
            self.attempts[attempt_id] = (digests, now, False)
            return attempt_id

    def mark_login_attempt_failed(self, attempt_id: str) -> None:
        with self._lock:
            row = self.attempts.get(attempt_id)
            if row is not None:
                self.attempts[attempt_id] = (row[0], row[1], True)

    def release_login_attempt(self, attempt_id: str) -> None:
        with self._lock:
            self.attempts.pop(attempt_id, None)

    def rotate_csrf(self, token_hash: str, csrf_hash: str, now: datetime) -> bool:
        with self._lock:
            row = self.sessions.get(token_hash)
            if row is None or row[3] or row[2] <= now:
                return False
            self.sessions[token_hash] = (row[0], csrf_hash, row[2], row[3])
            return True
