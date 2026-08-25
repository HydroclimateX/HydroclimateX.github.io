from __future__ import annotations

from datetime import date, datetime
from json import dumps

import psycopg
from psycopg.rows import dict_row

from .repository import AdminSession, EventConflict, MonthlyReport, UsageEvent


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def migrate(self) -> None:
        from pathlib import Path

        migration = (Path(__file__).with_name("migrations") / "001_initial.sql").read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.execute(migration)

    def create_session(self, session: AdminSession) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_sessions (token_hash, csrf_token, email, expires_at, revoked)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (token_hash) DO UPDATE SET
                    csrf_token = EXCLUDED.csrf_token,
                    email = EXCLUDED.email,
                    expires_at = EXCLUDED.expires_at,
                    revoked = EXCLUDED.revoked
                """,
                (session.token_hash, session.csrf_token, session.email, session.expires_at, session.revoked),
            )

    def get_session(self, token_hash: str, now: datetime) -> AdminSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT token_hash, csrf_token, email, expires_at, revoked
                FROM admin_sessions
                WHERE token_hash = %s AND revoked = FALSE AND expires_at > %s
                """,
                (token_hash, now),
            ).fetchone()
        return AdminSession(**row) if row else None

    def revoke_session(self, token_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE admin_sessions SET revoked = TRUE WHERE token_hash = %s", (token_hash,))

    def revoke_all_sessions(self) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE admin_sessions SET revoked = TRUE WHERE revoked = FALSE")

    def record_audit(self, action: str, result: str, occurred_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO admin_audit (action, result, occurred_at) VALUES (%s, %s, %s)",
                (action, result, occurred_at),
            )

    def record_event(self, event: UsageEvent) -> bool:
        with self._connect() as connection:
            if event.event_type == "download":
                succeeded = connection.execute(
                    "SELECT 1 FROM wasp_events WHERE run_id = %s AND event_type = 'run_success'",
                    (event.run_id,),
                ).fetchone()
                if not succeeded:
                    raise EventConflict("download requires a successful run")

            cursor = connection.execute(
                """
                INSERT INTO wasp_events (event_type, session_hash, country_code, occurred_at, run_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (event.event_type, event.session_hash, event.country_code, event.occurred_at, event.run_id),
            )
            if cursor.fetchone():
                return True
            if event.event_type in {"run_success", "run_failure"}:
                existing = connection.execute(
                    """
                    SELECT event_type, session_hash, country_code, occurred_at, run_id::text AS run_id
                    FROM wasp_events WHERE run_id = %s AND event_type IN ('run_success', 'run_failure')
                    """,
                    (event.run_id,),
                ).fetchone()
                if not existing or existing["event_type"] != event.event_type:
                    raise EventConflict("run already has an outcome")
            return False

    def events_between(self, start: datetime, end: datetime) -> list[dict[str, object]]:
        with self._connect() as connection:
            return list(connection.execute(
                """
                SELECT event_type, session_hash, country_code,
                       occurred_at, run_id::text AS run_id
                FROM wasp_events
                WHERE occurred_at >= %s AND occurred_at < %s
                ORDER BY occurred_at
                """,
                (start, end),
            ).fetchall())

    def get_report(self, report_month: date) -> MonthlyReport | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT report_month, snapshot, status, generated_at, sent_at, message_id, failure_code
                FROM monthly_reports WHERE report_month = %s
                """,
                (report_month,),
            ).fetchone()
        return MonthlyReport(**row) if row else None

    def save_report(self, report: MonthlyReport) -> MonthlyReport:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monthly_reports
                    (report_month, snapshot, status, generated_at, sent_at, message_id, failure_code)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s)
                ON CONFLICT (report_month) DO UPDATE SET
                    snapshot = monthly_reports.snapshot,
                    status = CASE WHEN monthly_reports.status = 'sent' THEN monthly_reports.status ELSE EXCLUDED.status END,
                    generated_at = monthly_reports.generated_at,
                    sent_at = COALESCE(EXCLUDED.sent_at, monthly_reports.sent_at),
                    message_id = COALESCE(EXCLUDED.message_id, monthly_reports.message_id),
                    failure_code = EXCLUDED.failure_code
                """,
                (
                    report.report_month, dumps(report.snapshot), report.status, report.generated_at,
                    report.sent_at, report.message_id, report.failure_code,
                ),
            )
        return self.get_report(report.report_month) or report

    def claim_report_delivery(self, report_month: date, *, force: bool = False) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE monthly_reports SET status = 'sending'
                WHERE report_month = %s
                  AND status <> 'sending'
                  AND (status <> 'sent' OR %s)
                RETURNING report_month
                """,
                (report_month, force),
            ).fetchone()
        return row is not None
