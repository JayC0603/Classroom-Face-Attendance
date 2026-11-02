from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from control_api.auth_domain import AuthService, InMemoryAuthRepository, PasswordHasher
from control_api.auth_postgres import PostgresAuthRepository


def test_password_hash_is_versioned_scrypt_and_constant_time_verifiable() -> None:
    hasher = PasswordHasher()
    encoded = hasher.hash("Correct horse battery 9!")

    assert encoded.startswith("scrypt$v1$")
    assert hasher.verify("Correct horse battery 9!", encoded)
    assert not hasher.verify("wrong", encoded)


def test_login_returns_generic_error_for_unknown_and_wrong_password() -> None:
    service = AuthService(InMemoryAuthRepository(), session_ttl=timedelta(hours=1))
    service.bootstrap_admin("admin@example.test", "A strong password 9!")

    with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
        service.login("missing@example.test", "anything", "127.0.0.1")
    with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
        service.login("admin@example.test", "wrong", "127.0.0.1")


def test_sessions_rotate_expire_and_revoke() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo = InMemoryAuthRepository()
    service = AuthService(repo, clock=lambda: now, session_ttl=timedelta(seconds=30))
    service.bootstrap_admin("admin@example.test", "A strong password 9!")
    first = service.login("admin@example.test", "A strong password 9!", "127.0.0.1")
    second = service.login("admin@example.test", "A strong password 9!", "127.0.0.1")

    assert first.session_token != second.session_token
    assert service.resolve_session(first.session_token) is None
    assert service.resolve_session(second.session_token) == second.principal
    service.logout(second.session_token)
    assert service.resolve_session(second.session_token) is None

    third = service.login("admin@example.test", "A strong password 9!", "127.0.0.1")
    now += timedelta(seconds=31)
    assert service.resolve_session(third.session_token) is None


def test_bootstrap_admin_is_one_time_and_account_role_person_link_is_validated() -> None:
    repo = InMemoryAuthRepository()
    service = AuthService(repo)
    service.bootstrap_admin("root@example.test", "A strong password 9!")
    with pytest.raises(ValueError, match="ADMIN_ALREADY_EXISTS"):
        service.bootstrap_admin("other@example.test", "A strong password 9!")
    with pytest.raises(ValueError, match="PERSON_ID_ONLY_FOR_STUDENT"):
        service.create_account("teacher@example.test", "Teacher Pass 9!", "TEACHER", "person-1")


def test_unknown_person_foreign_key_is_a_clean_validation_error(monkeypatch) -> None:
    class ForeignKeyViolation(Exception):
        sqlstate = "23503"

    repository = PostgresAuthRepository("postgresql://unused")

    def fail_insert(*_args, **_kwargs):
        raise ForeignKeyViolation

    monkeypatch.setattr(repository, "_run", fail_insert)
    with pytest.raises(ValueError, match="PERSON_NOT_FOUND"):
        repository.create_user("student@example.test", "hash", "STUDENT", "missing-person")


def test_login_throttle_applies_to_normalized_account_and_remote_address() -> None:
    service = AuthService(
        InMemoryAuthRepository(), max_login_attempts=2, login_window=timedelta(minutes=5)
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
            service.login("nobody@example.test", "wrong", "127.0.0.1")
    with pytest.raises(ValueError, match="LOGIN_RATE_LIMITED"):
        service.login("nobody@example.test", "wrong", "127.0.0.1")


def test_valid_login_does_not_clear_shared_ip_failure_bucket() -> None:
    service = AuthService(
        InMemoryAuthRepository(),
        max_login_attempts=3,
        login_window=timedelta(minutes=5),
    )
    service.bootstrap_admin("admin@example.test", "A strong password 9!")
    for _ in range(2):
        with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
            service.login("missing@example.test", "wrong", "192.0.2.8")
    service.login("admin@example.test", "A strong password 9!", "192.0.2.8")
    with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
        service.login("another-missing@example.test", "wrong", "192.0.2.8")
    with pytest.raises(ValueError, match="LOGIN_RATE_LIMITED"):
        service.login("another-missing@example.test", "wrong", "192.0.2.8")


def test_login_reservations_are_atomic_and_success_releases_only_its_reservation() -> None:
    repo = InMemoryAuthRepository()
    service = AuthService(repo, max_login_attempts=3)
    service.bootstrap_admin("admin@example.test", "A strong password 9!")
    digests = service._rate_digests("admin@example.test", "192.0.2.10")

    with ThreadPoolExecutor(max_workers=12) as pool:
        reservations = list(
            pool.map(
                lambda _item: repo.reserve_login_attempt(
                    digests, service.clock(), service.login_window, service.max_login_attempts
                ),
                range(12),
            )
        )
    held = [attempt_id for attempt_id in reservations if attempt_id is not None]
    assert len(held) == 3
    repo.mark_login_attempt_failed(held[0])
    for attempt_id in held[1:]:
        repo.release_login_attempt(attempt_id)
    assert list(repo.attempts) == [held[0]]

    # A successful login reserves before scrypt, then removes only its own reservation.
    service.login("admin@example.test", "A strong password 9!", "192.0.2.11")
    assert list(repo.attempts) == [held[0]]


def test_login_cannot_issue_session_after_concurrent_password_reset() -> None:
    class RacingRepository(InMemoryAuthRepository):
        password_hash_after_verify: str | None = None

        def issue_session(
            self,
            user_id: str,
            token_hash: str,
            csrf_hash: str,
            expires_at: datetime,
            expected_password_hash: str,
        ) -> None:
            if self.password_hash_after_verify is not None:
                self.update_password_and_revoke_sessions(user_id, self.password_hash_after_verify)
                self.password_hash_after_verify = None
            super().issue_session(
                user_id, token_hash, csrf_hash, expires_at, expected_password_hash
            )

    repo = RacingRepository()
    service = AuthService(repo)
    user = service.bootstrap_admin("admin@example.test", "A strong password 9!")
    repo.password_hash_after_verify = "new-password-hash-from-concurrent-reset"

    with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
        service.login("admin@example.test", "A strong password 9!", "127.0.0.1")
    assert not repo.sessions
    assert repo.user_by_id(user.id).password_hash == "new-password-hash-from-concurrent-reset"


def test_current_rotates_csrf_and_rate_limit_preflight_avoids_scrypt() -> None:
    service = AuthService(InMemoryAuthRepository(), max_login_attempts=1)
    service.bootstrap_admin("admin@example.test", "A strong password 9!")
    with pytest.raises(ValueError, match="INVALID_CREDENTIALS"):
        service.login("missing@example.test", "wrong", "192.0.2.9")

    def should_not_verify(*_args) -> bool:
        raise AssertionError("blocked login must not execute scrypt verification")

    service.hasher.verify = should_not_verify
    with pytest.raises(ValueError, match="LOGIN_RATE_LIMITED"):
        service.login("missing@example.test", "wrong", "192.0.2.9")

    service.hasher.verify = PasswordHasher().verify
    login = service.login("admin@example.test", "A strong password 9!", "192.0.2.10")
    current = service.current(login.session_token)
    assert current is not None
    principal, new_csrf = current
    assert principal == login.principal
    assert new_csrf != login.csrf_token
    assert not service.verify_csrf(login.csrf_token, login.session_token)
    assert service.verify_csrf(new_csrf, login.session_token)
