from __future__ import annotations

import hmac
import csv
import io
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .domain import PeriodError, aggregate_country_rows, format_timestamp_seconds, resolve_period
from .repository import AdminSession, EventConflict, Repository, UsageEvent
from .security import LoginLimiter, create_session_credentials, hash_token, verify_password


SESSION_COOKIE = "hx_analytics_session"


class LoginRequest(BaseModel):
    email: str
    password: str


class EventRequest(BaseModel):
    event_type: Literal["session_start", "run_success", "run_failure", "download"]
    session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    country_code: str = Field(pattern=r"^[A-Za-z]{2}$")
    occurred_at: datetime
    run_id: UUID | None = None


class SendReportRequest(BaseModel):
    force: bool = False


def parse_report_month(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="report month must use YYYY-MM") from exc
    return parsed


def create_app(*, settings: Settings, repository: Repository, umami, report_service=None) -> FastAPI:
    app = FastAPI(title="HydroclimateX Analytics API", docs_url=None, redoc_url=None)
    limiter = LoginLimiter()
    if report_service is None:
        from .reports import ReportService
        report_service = ReportService(settings, repository, umami)
    static_root = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_root), name="analytics-static")

    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def authenticated_session(
        raw_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> AdminSession:
        if not raw_token:
            raise HTTPException(status_code=401, detail="authentication required")
        session = repository.get_session(hash_token(raw_token), now_utc())
        if not session:
            raise HTTPException(status_code=401, detail="authentication required")
        return session

    def require_csrf(
        session: AdminSession = Depends(authenticated_session),
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> AdminSession:
        if not csrf or not hmac.compare_digest(session.csrf_token, csrf):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        return session

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "analytics"}

    @app.get("/public/telemetry-config")
    def telemetry_config(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "public, max-age=300"
        return {"websiteId": settings.umami_website_id}

    @app.get("/", include_in_schema=False)
    def dashboard_shell() -> FileResponse:
        return FileResponse(static_root / "index.html", headers={"Cache-Control": "no-store"})

    @app.post("/auth/login")
    def login(payload: LoginRequest, response: Response) -> dict[str, str]:
        account = payload.email.strip().lower()
        now = now_utc()
        if limiter.is_locked(account, now):
            raise HTTPException(status_code=429, detail="too many login attempts")
        valid = account == settings.admin_email.lower() and verify_password(
            settings.admin_password_hash, payload.password
        )
        if not valid:
            limiter.record_failure(account, now)
            repository.record_audit("login_failure", "denied", now)
            raise HTTPException(status_code=401, detail="invalid credentials")
        limiter.record_success(account)
        credentials = create_session_credentials()
        repository.create_session(AdminSession(
            credentials.token_hash,
            credentials.csrf_token,
            settings.admin_email,
            now + timedelta(hours=settings.session_hours),
        ))
        repository.record_audit("login_success", "allowed", now)
        response.set_cookie(
            SESSION_COOKIE,
            credentials.raw_token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return {"email": settings.admin_email, "csrf_token": credentials.csrf_token}

    @app.get("/auth/session")
    def session_details(
        session: AdminSession = Depends(authenticated_session),
    ) -> dict[str, str]:
        return {"email": session.email, "csrf_token": session.csrf_token}

    @app.post("/auth/logout", status_code=204)
    def logout(
        response: Response,
        session: AdminSession = Depends(require_csrf),
    ) -> Response:
        repository.revoke_session(session.token_hash)
        repository.record_audit("logout", "success", now_utc())
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        response.status_code = 204
        return response

    @app.post("/internal/v1/wasp-events", status_code=202)
    def ingest_event(
        payload: EventRequest,
        token: Annotated[str | None, Header(alias="X-Analytics-Token")] = None,
    ) -> dict[str, bool]:
        if not token or not hmac.compare_digest(token, settings.internal_token):
            raise HTTPException(status_code=401, detail="invalid service token")
        if payload.event_type != "session_start" and payload.run_id is None:
            raise HTTPException(status_code=422, detail="run_id is required")
        try:
            created = repository.record_event(UsageEvent(
                payload.event_type,
                payload.session_hash,
                payload.country_code.upper(),
                payload.occurred_at.astimezone(timezone.utc),
                str(payload.run_id) if payload.run_id else None,
            ))
        except EventConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"created": created}

    def selected_period(
        period: str = Query("30d"),
        start: str | None = Query(None),
        end: str | None = Query(None),
    ):
        try:
            return resolve_period(
                period,
                start=start,
                end=end,
                collected_since=settings.collected_since,
            )
        except PeriodError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/summary")
    def summary(
        _: AdminSession = Depends(authenticated_session),
        period=Depends(selected_period),
    ) -> dict[str, object]:
        website = umami.summary(period)
        checked_at = now_utc()
        try:
            event_rows = repository.events_between(period.start, period.end)
            wasp = aggregate_country_rows(event_rows)
            totals = wasp["totals"]
            wasp_status = "available"
            raw_last_activity = max(
                (row.get("occurred_at") for row in event_rows if row.get("occurred_at")),
                default=None,
            )
            wasp_last_activity = format_timestamp_seconds(raw_last_activity) if raw_last_activity is not None else None
        except Exception:
            totals = {"successful_runs": None, "success_rate": None, "countries": None}
            wasp_status = "unavailable"
            wasp_last_activity = None
        return {
            "period": {"key": period.key, "label": period.label, "start": period.start, "end": period.end},
            "collected_since": settings.collected_since,
            "sources": {"website": website.get("status", "unavailable"), "wasp": wasp_status},
            "source_freshness": {
                "website": {"checked_at": checked_at},
                "wasp": {"checked_at": checked_at, "last_activity": wasp_last_activity},
            },
            "kpis": {
                "visitors": website.get("visitors"),
                "pageviews": website.get("pageviews"),
                "successful_runs": totals["successful_runs"],
                "success_rate": totals["success_rate"],
                "countries": totals["countries"],
            },
        }

    @app.get("/api/v1/website/windows")
    def website_windows(
        _: AdminSession = Depends(authenticated_session),
    ) -> dict[str, object]:
        return umami.website_windows(now_utc())

    def wasp_usage(period) -> dict[str, object]:
        try:
            usage = aggregate_country_rows(repository.events_between(period.start, period.end))
            usage["status"] = "available"
            return usage
        except Exception:
            return {
                "status": "unavailable",
                "totals": {
                    "successful_runs": None, "failed_runs": None, "downloads": None,
                    "sessions": None, "countries": None, "success_rate": None,
                },
                "countries": [],
            }

    @app.get("/api/v1/wasp/countries")
    def countries(
        _: AdminSession = Depends(authenticated_session),
        period=Depends(selected_period),
    ) -> dict[str, object]:
        usage = wasp_usage(period)
        return {
            "period": {"key": period.key, "label": period.label, "start": period.start, "end": period.end},
            **usage,
        }

    @app.get("/api/v1/wasp/map.png")
    def usage_map_png(
        _: AdminSession = Depends(authenticated_session),
        period=Depends(selected_period),
        metric: str = Query("successful_runs"),
    ) -> Response:
        usage = wasp_usage(period)
        if usage["status"] != "available":
            raise HTTPException(status_code=503, detail="WASP analytics source unavailable")
        from .map_render import render_usage_map

        png = render_usage_map(usage["countries"], metric)
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/wasp/countries/{country_code}")
    def country_detail(
        country_code: str,
        _: AdminSession = Depends(authenticated_session),
        period=Depends(selected_period),
    ) -> dict[str, object]:
        code = country_code.upper()
        usage = wasp_usage(period)
        if usage["status"] != "available":
            raise HTTPException(status_code=503, detail="WASP analytics source unavailable")
        row = next((item for item in usage["countries"] if item["country_code"] == code), None)
        if row is None:
            raise HTTPException(status_code=404, detail="country not found")
        return row

    @app.get("/api/v1/wasp/export.csv")
    def export_csv(
        _: AdminSession = Depends(authenticated_session),
        period=Depends(selected_period),
    ) -> StreamingResponse:
        usage = wasp_usage(period)
        if usage["status"] != "available":
            raise HTTPException(status_code=503, detail="WASP analytics source unavailable")
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "Country Code", "Country", "Successful Runs", "Failed Runs",
            "Downloads", "Sessions", "Last Activity",
        ])
        for row in usage["countries"]:
            writer.writerow([
                row["country_code"], row["country"], row["successful_runs"],
                row["failed_runs"], row["downloads"], row["sessions"], row["last_activity"],
            ])
        filename = f"hydroclimatex_usage_{period.key}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/v1/reports/{report_month}")
    def report_preview(
        report_month: str,
        _: AdminSession = Depends(authenticated_session),
    ) -> dict[str, object]:
        report = report_service.generate(parse_report_month(report_month))
        return {
            "month": report.report_month,
            "status": report.status,
            "generated_at": report.generated_at,
            "sent_at": report.sent_at,
            "message_id": report.message_id,
            "failure_code": report.failure_code,
            "snapshot": report.snapshot,
        }

    @app.post("/api/v1/reports/{report_month}/send")
    def report_send(
        report_month: str,
        payload: SendReportRequest,
        _: AdminSession = Depends(require_csrf),
    ) -> dict[str, object]:
        result = report_service.send(parse_report_month(report_month), force=payload.force)
        repository.record_audit("report_send", "sent" if result.get("sent") else "deduplicated", now_utc())
        return result

    return app


def production_app() -> FastAPI:
    from .repository_postgres import PostgresRepository
    from .umami import UmamiClient

    settings = Settings.from_env()
    return create_app(
        settings=settings,
        repository=PostgresRepository(settings.database_url),
        umami=UmamiClient(settings),
    )


app = None
