from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


class EventConflict(ValueError):
    pass


@dataclass(frozen=True)
class UsageEvent:
    event_type: str
    session_hash: str
    country_code: str
    occurred_at: datetime
    run_id: str | None = None


@dataclass(frozen=True)
class AdminSession:
    token_hash: str
    csrf_token: str
    email: str
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True)
class MonthlyReport:
    report_month: date
    snapshot: dict[str, object]
    status: str
    generated_at: datetime
    sent_at: datetime | None = None
    message_id: str | None = None
    failure_code: str | None = None


class Repository(Protocol):
    def create_session(self, session: AdminSession) -> None: ...
    def get_session(self, token_hash: str, now: datetime) -> AdminSession | None: ...
    def revoke_session(self, token_hash: str) -> None: ...
    def revoke_all_sessions(self) -> None: ...
    def record_audit(self, action: str, result: str, occurred_at: datetime) -> None: ...
    def record_event(self, event: UsageEvent) -> bool: ...
    def events_between(self, start: datetime, end: datetime) -> list[dict[str, object]]: ...
    def get_report(self, report_month: date) -> MonthlyReport | None: ...
    def save_report(self, report: MonthlyReport) -> MonthlyReport: ...
    def claim_report_delivery(self, report_month: date, *, force: bool = False) -> bool: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, AdminSession] = {}
        self.events: list[UsageEvent] = []
        self.reports: dict[date, MonthlyReport] = {}
        self.audits: list[tuple[str, str, datetime]] = []

    def create_session(self, session: AdminSession) -> None:
        self.sessions[session.token_hash] = session

    def get_session(self, token_hash: str, now: datetime) -> AdminSession | None:
        session = self.sessions.get(token_hash)
        if session is None or session.revoked or session.expires_at <= now:
            return None
        return session

    def revoke_session(self, token_hash: str) -> None:
        session = self.sessions.get(token_hash)
        if session:
            self.sessions[token_hash] = AdminSession(
                session.token_hash, session.csrf_token, session.email, session.expires_at, True
            )

    def revoke_all_sessions(self) -> None:
        for token_hash in list(self.sessions):
            self.revoke_session(token_hash)

    def record_audit(self, action: str, result: str, occurred_at: datetime) -> None:
        self.audits.append((action, result, occurred_at))

    def record_event(self, event: UsageEvent) -> bool:
        if event.event_type == "download":
            succeeded = any(
                row.run_id == event.run_id and row.event_type == "run_success"
                for row in self.events
            )
            if not succeeded:
                raise EventConflict("download requires a successful run")
        if event.event_type in {"run_success", "run_failure"}:
            existing_outcome = next((
                row for row in self.events
                if row.run_id == event.run_id and row.event_type in {"run_success", "run_failure"}
            ), None)
            if existing_outcome:
                if existing_outcome == event:
                    return False
                raise EventConflict("run already has an outcome")
        if event in self.events:
            return False
        self.events.append(event)
        return True

    def events_between(self, start: datetime, end: datetime) -> list[dict[str, object]]:
        return [
            {
                "event_type": row.event_type,
                "session_hash": row.session_hash,
                "country_code": row.country_code,
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "run_id": row.run_id,
            }
            for row in self.events if start <= row.occurred_at.astimezone(start.tzinfo) < end
        ]

    def seed_event(
        self,
        event_type: str,
        session_hash: str,
        country_code: str,
        occurred_at: str,
        *,
        run_id: str | None = None,
    ) -> None:
        self.record_event(UsageEvent(
            event_type,
            session_hash,
            country_code,
            datetime.fromisoformat(occurred_at.replace("Z", "+00:00")),
            run_id,
        ))

    def get_report(self, report_month: date) -> MonthlyReport | None:
        return self.reports.get(report_month)

    def save_report(self, report: MonthlyReport) -> MonthlyReport:
        existing = self.reports.get(report.report_month)
        if existing and existing.status == "sent" and report.status != "sent":
            return existing
        if existing:
            report = MonthlyReport(
                existing.report_month,
                existing.snapshot,
                report.status,
                existing.generated_at,
                report.sent_at,
                report.message_id,
                report.failure_code,
            )
        self.reports[report.report_month] = report
        return report

    def claim_report_delivery(self, report_month: date, *, force: bool = False) -> bool:
        report = self.reports.get(report_month)
        if report is None or report.status == "sending" or (report.status == "sent" and not force):
            return False
        self.reports[report_month] = MonthlyReport(
            report.report_month, report.snapshot, "sending", report.generated_at,
            report.sent_at, report.message_id, report.failure_code,
        )
        return True
