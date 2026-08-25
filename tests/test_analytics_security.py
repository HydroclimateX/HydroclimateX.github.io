from datetime import datetime, timedelta, timezone

import pytest

from analytics_app.security import (
    LoginLimiter,
    create_session_credentials,
    hash_password,
    hash_token,
    verify_password,
)
from analytics_app.repository import AdminSession, MemoryRepository


def test_argon2id_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "correct horse battery staple") is True
    assert verify_password(encoded, "wrong") is False


def test_malformed_argon2_hash_is_an_invalid_login_not_a_server_error() -> None:
    malformed = "$argon2id$v=19$m=65536,t=3,p=4$short$salt"

    assert verify_password(malformed, "0123456789") is False


def test_administrator_password_accepts_ten_characters_and_rejects_nine() -> None:
    encoded = hash_password("0123456789")

    assert verify_password(encoded, "0123456789") is True
    with pytest.raises(ValueError, match="at least 10 characters"):
        hash_password("012345678")


def test_session_credentials_store_only_token_hash() -> None:
    credentials = create_session_credentials()

    assert credentials.raw_token != credentials.token_hash
    assert credentials.csrf_token
    assert hash_token(credentials.raw_token) == credentials.token_hash


def test_login_limiter_locks_after_five_failures_without_using_ip() -> None:
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    limiter = LoginLimiter(max_failures=5, lock_for=timedelta(minutes=15))

    for _ in range(5):
        limiter.record_failure("ze.jiang@hhu.edu.cn", now)

    assert limiter.is_locked("ze.jiang@hhu.edu.cn", now + timedelta(minutes=14))
    assert not limiter.is_locked("ze.jiang@hhu.edu.cn", now + timedelta(minutes=16))


def test_successful_login_clears_failure_count() -> None:
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    limiter = LoginLimiter(max_failures=2, lock_for=timedelta(minutes=15))
    limiter.record_failure("ze.jiang@hhu.edu.cn", now)

    limiter.record_success("ze.jiang@hhu.edu.cn")
    limiter.record_failure("ze.jiang@hhu.edu.cn", now)

    assert not limiter.is_locked("ze.jiang@hhu.edu.cn", now)


def test_password_reset_can_revoke_every_server_side_session() -> None:
    repository = MemoryRepository()
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    repository.create_session(AdminSession("a" * 64, "csrf", "admin@example.com", now + timedelta(hours=12)))

    repository.revoke_all_sessions()

    assert repository.get_session("a" * 64, now) is None
