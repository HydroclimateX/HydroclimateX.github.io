"""Privacy-safe usage events for the LISFLOOD web service.

Mirrors ``backend/usage.py``: only the event type, an HMAC of the browser
session token, a two-letter country code, a UTC timestamp and the job
identifier ever leave this process.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue
import secrets
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Callable


LOGGER = logging.getLogger("lisflood.usage")

DEFAULT_ENDPOINT = "http://analytics-api:8001/internal/v1/lisflood-events"
QUEUE_SIZE = 64
TIMEOUT_SECONDS = 1.0


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_hash(raw_token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def country_from_ip(value: str | None, *, reader=None) -> str:
    if not value or reader is None:
        return "ZZ"
    try:
        ipaddress.ip_address(value)
        record = reader.get(value)
        country = record.get("country") if isinstance(record, dict) else None
        code = country.get("iso_code") if isinstance(country, dict) else None
    except Exception:
        return "ZZ"
    normalized = str(code).upper() if code else ""
    return normalized if len(normalized) == 2 and normalized.isascii() and normalized.isalpha() else "ZZ"


def _post_json(endpoint: str, token: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Analytics-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status not in {200, 201, 202}:
            raise OSError("analytics collector rejected event")


class UsageTracker:
    """Queue usage events onto one bounded daemon thread, never the request path.

    The tracker is inert unless the analytics endpoint, the internal token and a
    session secret are all configured, so a missing analytics deployment simply
    means no events.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        internal_token: str,
        session_secret: str,
        sender: Callable[[str, str, dict[str, object]], None] = _post_json,
        geoip_reader=None,
        queue_size: int = QUEUE_SIZE,
    ) -> None:
        self.endpoint = endpoint
        self.internal_token = internal_token
        self.session_secret = session_secret
        self.sender = sender
        self.geoip_reader = geoip_reader
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "UsageTracker":
        reader = None
        database_path = os.getenv("ANALYTICS_GEOIP_DATABASE", "").strip()
        if database_path:
            try:
                # ponytail: maxminddb is a deliberate optional dependency — the
                # public standalone copy of this module ships without it, so a
                # missing package or database only costs the country code.
                import maxminddb
                reader = maxminddb.open_database(database_path)
            except Exception:
                LOGGER.warning("DB-IP country database is unavailable")
        return cls(
            endpoint=os.getenv("ANALYTICS_INTERNAL_EVENTS_URL", DEFAULT_ENDPOINT),
            internal_token=os.getenv("ANALYTICS_INTERNAL_TOKEN", ""),
            session_secret=os.getenv("ANALYTICS_SESSION_SECRET", "development-only-session-secret"),
            geoip_reader=reader,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint and self.internal_token and len(self.session_secret) >= 32)

    def country(self, client_ip: str | None) -> str:
        return country_from_ip(client_ip, reader=self.geoip_reader)

    def emit(
        self,
        event_type: str,
        *,
        session_hash: str,
        country_code: str,
        occurred_at: datetime | None = None,
        run_id: str | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        try:
            payload = {
                "event_type": event_type,
                "session_hash": session_hash,
                "country_code": country_code if isinstance(country_code, str) and len(country_code) == 2 else "ZZ",
                "occurred_at": (occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "run_id": run_id,
            }
            self._ensure_worker()
            self._queue.put_nowait(payload)
        except Exception:
            return False
        return True

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._drain,
                    name="lisflood-usage",
                    daemon=True,
                )
                self._worker.start()

    def _drain(self) -> None:
        while True:
            payload = self._queue.get()
            try:
                self.sender(self.endpoint, self.internal_token, payload)
            except Exception:
                LOGGER.warning("Analytics event delivery is unavailable")
