from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
MIN_ADMIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"administrator password must contain at least {MIN_ADMIN_PASSWORD_LENGTH} characters"
        )
    return _PASSWORD_HASHER.hash(password)


def verify_password(encoded: str, candidate: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(encoded, candidate)
    except (UnicodeEncodeError, VerificationError, VerifyMismatchError, InvalidHashError):
        return False


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionCredentials:
    raw_token: str
    token_hash: str
    csrf_token: str


def create_session_credentials() -> SessionCredentials:
    raw_token = secrets.token_urlsafe(48)
    return SessionCredentials(raw_token, hash_token(raw_token), secrets.token_urlsafe(32))


class LoginLimiter:
    def __init__(self, *, max_failures: int = 5, lock_for: timedelta = timedelta(minutes=15)) -> None:
        self.max_failures = max_failures
        self.lock_for = lock_for
        self._attempts: dict[str, list[datetime]] = {}

    def _active_failures(self, account: str, now: datetime) -> list[datetime]:
        cutoff = now - self.lock_for
        active = [attempt for attempt in self._attempts.get(account, []) if attempt > cutoff]
        if active:
            self._attempts[account] = active
        else:
            self._attempts.pop(account, None)
        return active

    def is_locked(self, account: str, now: datetime) -> bool:
        return len(self._active_failures(account, now)) >= self.max_failures

    def record_failure(self, account: str, now: datetime) -> None:
        active = self._active_failures(account, now)
        active.append(now)
        self._attempts[account] = active

    def record_success(self, account: str) -> None:
        self._attempts.pop(account, None)
