from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import urllib.request
from datetime import datetime, timezone
from typing import Callable


LOGGER = logging.getLogger("wasp.usage")


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
    with urllib.request.urlopen(request, timeout=1.0) as response:
        if response.status not in {200, 201, 202}:
            raise OSError("analytics collector rejected event")


class UsageTracker:
    def __init__(
        self,
        *,
        endpoint: str,
        internal_token: str,
        session_secret: str,
        sender: Callable[[str, str, dict[str, object]], None] = _post_json,
        geoip_reader=None,
    ) -> None:
        self.endpoint = endpoint
        self.internal_token = internal_token
        self.session_secret = session_secret
        self.sender = sender
        self.geoip_reader = geoip_reader

    @classmethod
    def from_env(cls) -> "UsageTracker":
        reader = None
        database_path = os.getenv("ANALYTICS_GEOIP_DATABASE", "").strip()
        if database_path:
            try:
                import maxminddb
                reader = maxminddb.open_database(database_path)
            except Exception:
                LOGGER.warning("DB-IP country database is unavailable")
        return cls(
            endpoint=os.getenv(
                "ANALYTICS_INTERNAL_EVENTS_URL",
                "http://analytics-api:8001/internal/v1/wasp-events",
            ),
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
        session_token: str,
        country_code: str,
        run_id: str | None,
        occurred_at: datetime | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        payload = {
            "event_type": event_type,
            "session_hash": session_hash(session_token, self.session_secret),
            "country_code": country_code if len(country_code) == 2 else "ZZ",
            "occurred_at": (occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
        }
        try:
            self.sender(self.endpoint, self.internal_token, payload)
        except Exception:
            LOGGER.warning("Analytics event delivery is unavailable")
            return False
        return True
