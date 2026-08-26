from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    admin_email: str
    admin_password_hash: str
    internal_token: str
    session_secret: str
    collected_since: datetime
    database_url: str = ""
    umami_base_url: str = "http://umami:3000"
    umami_username: str = ""
    umami_password: str = ""
    umami_website_id: str = ""
    umami_admin_username: str = "admin"
    umami_admin_password: str = "umami"
    session_hours: int = 12
    smtp_host: str = "smtp.126.com"
    smtp_port: int = 465
    smtp_username: str = "zejiang_hydrology@126.com"
    smtp_password: str = ""
    report_from: str = "zejiang_hydrology@126.com"
    report_to: str = "ze.jiang@hhu.edu.cn"

    def __post_init__(self) -> None:
        if len(self.internal_token) < 32 or len(self.session_secret) < 32:
            raise ValueError("internal and session secrets must contain at least 32 characters")
        if self.collected_since.tzinfo is None:
            raise ValueError("collected_since must be timezone-aware")

    @classmethod
    def from_env(cls) -> "Settings":
        raw_since = os.getenv("ANALYTICS_COLLECTED_SINCE", "").strip()
        collected_since = (
            datetime.fromisoformat(raw_since.replace("Z", "+00:00"))
            if raw_since else datetime.now(timezone.utc)
        )
        return cls(
            admin_email=os.getenv("ANALYTICS_ADMIN_EMAIL", "ze.jiang@hhu.edu.cn").strip().lower(),
            admin_password_hash=_required("ANALYTICS_ADMIN_PASSWORD_HASH"),
            internal_token=_required("ANALYTICS_INTERNAL_TOKEN"),
            session_secret=_required("ANALYTICS_SESSION_SECRET"),
            collected_since=collected_since,
            database_url=_required("ANALYTICS_DATABASE_URL"),
            umami_base_url=os.getenv("UMAMI_BASE_URL", "http://umami:3000").rstrip("/"),
            umami_username=os.getenv("UMAMI_API_USERNAME", "").strip(),
            umami_password=os.getenv("UMAMI_API_PASSWORD", "").strip(),
            umami_website_id=os.getenv("UMAMI_WEBSITE_ID", "").strip(),
            umami_admin_username=os.getenv("UMAMI_ADMIN_USERNAME", "admin").strip(),
            umami_admin_password=os.getenv("UMAMI_ADMIN_PASSWORD", "umami").strip(),
            smtp_host=os.getenv("ANALYTICS_SMTP_HOST", "smtp.126.com"),
            smtp_port=int(os.getenv("ANALYTICS_SMTP_PORT", "465")),
            smtp_username=os.getenv("ANALYTICS_SMTP_USERNAME", "zejiang_hydrology@126.com"),
            smtp_password=_required("ANALYTICS_SMTP_AUTHORIZATION_CODE"),
            report_from=os.getenv("ANALYTICS_REPORT_FROM", "zejiang_hydrology@126.com"),
            report_to=os.getenv("ANALYTICS_REPORT_TO", "ze.jiang@hhu.edu.cn"),
        )
