from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from analytics_app.config import Settings
from analytics_app.main import create_app
from analytics_app.repository import MemoryRepository, MonthlyReport
from analytics_app.security import hash_password


class FakeUmami:
    def summary(self, _period):
        return {"status": "available", "visitors": 1245, "pageviews": 3682, "countries": 46}

    def website_windows(self, _now):
        return {
            "status": "available",
            "metrics": [
                {"metric": "Visitors", "days_30": 1245, "months_12": 5000, "all_time": 5000},
            ],
        }


class FakeReports:
    def __init__(self, repository):
        self.repository = repository
        self.sent = []

    def generate(self, report_month):
        report = MonthlyReport(
            report_month,
            {"month": report_month.isoformat(), "label": "July 2026", "website": {"status": "available"}, "wasp": {"totals": {}, "countries": []}},
            "generated",
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        return self.repository.save_report(report)

    def send(self, report_month, force=False):
        self.sent.append((report_month, force))
        return {"sent": True, "message_id": "message-id"}


def make_client(*, reports=None) -> tuple[TestClient, MemoryRepository]:
    settings = Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash=hash_password("correct horse battery staple"),
        internal_token="internal-token-with-at-least-32-characters",
        session_secret="session-secret-with-at-least-32-characters",
        collected_since=datetime(2026, 8, 1, tzinfo=timezone.utc),
        umami_website_id="public-website-id",
    )
    repository = MemoryRepository()
    app = create_app(settings=settings, repository=repository, umami=FakeUmami(), report_service=reports or FakeReports(repository))
    return TestClient(app, base_url="https://analytics.hydroclimatex.test"), repository


def login(client: TestClient) -> str:
    response = client.post("/auth/login", json={
        "email": "ze.jiang@hhu.edu.cn",
        "password": "correct horse battery staple",
    })
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_api_rejects_anonymous_requests() -> None:
    client, _ = make_client()

    response = client.get("/api/v1/summary")

    assert response.status_code == 401


def test_public_telemetry_config_exposes_only_the_public_website_id() -> None:
    client, _ = make_client()

    response = client.get("/public/telemetry-config")

    assert response.status_code == 200
    assert response.json() == {"websiteId": "public-website-id"}
    assert response.headers["cache-control"] == "public, max-age=300"


def test_login_sets_hardened_cookie_and_exposes_session_csrf() -> None:
    client, _ = make_client()

    response = client.post("/auth/login", json={
        "email": "ze.jiang@hhu.edu.cn",
        "password": "correct horse battery staple",
    })
    csrf = response.json()["csrf_token"]

    cookie = client.cookies.get("hx_analytics_session")
    assert cookie
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    session = client.get("/auth/session")
    assert session.status_code == 200
    assert session.json() == {"email": "ze.jiang@hhu.edu.cn", "csrf_token": csrf}


def test_logout_requires_csrf_header() -> None:
    client, _ = make_client()
    csrf = login(client)

    assert client.post("/auth/logout").status_code == 403
    assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/v1/summary").status_code == 401
    assert [row[0] for row in _.audits] == ["login_success", "logout"]


def test_failed_login_audit_does_not_store_email_or_password() -> None:
    client, repository = make_client()

    response = client.post("/auth/login", json={"email": "attacker@example.com", "password": "wrong"})

    assert response.status_code == 401
    assert repository.audits[0][0] == "login_failure"
    assert "attacker" not in repr(repository.audits)
    assert "wrong" not in repr(repository.audits)


def test_internal_event_ingestion_is_token_protected_and_idempotent() -> None:
    client, repository = make_client()
    event = {
        "event_type": "run_success",
        "session_hash": "a" * 64,
        "country_code": "AU",
        "occurred_at": "2026-08-25T01:00:00Z",
        "run_id": "267eb25f-e2e5-4654-bf41-2bdcfbdedddc",
    }

    assert client.post("/internal/v1/wasp-events", json=event).status_code == 401
    headers = {"X-Analytics-Token": "internal-token-with-at-least-32-characters"}
    assert client.post("/internal/v1/wasp-events", json=event, headers=headers).json() == {"created": True}
    assert client.post("/internal/v1/wasp-events", json=event, headers=headers).json() == {"created": False}
    assert len(repository.events) == 1


def test_download_requires_a_successful_run() -> None:
    client, _ = make_client()
    headers = {"X-Analytics-Token": "internal-token-with-at-least-32-characters"}

    response = client.post("/internal/v1/wasp-events", headers=headers, json={
        "event_type": "download",
        "session_hash": "a" * 64,
        "country_code": "AU",
        "occurred_at": "2026-08-25T01:00:00Z",
        "run_id": "267eb25f-e2e5-4654-bf41-2bdcfbdedddc",
    })

    assert response.status_code == 409


def test_summary_combines_umami_and_wasp_metrics() -> None:
    client, repository = make_client()
    repository.seed_event("session_start", "s1", "AU", "2026-08-25T00:00:00Z")
    repository.seed_event("run_success", "s1", "AU", "2026-08-25T01:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")
    login(client)

    response = client.get("/api/v1/summary?period=30d")

    assert response.status_code == 200
    assert response.json()["kpis"] == {
        "visitors": 1245,
        "pageviews": 3682,
        "successful_runs": 1,
        "success_rate": 1.0,
        "countries": 1,
    }
    assert response.json()["sources"] == {"website": "available", "wasp": "available"}
    assert response.json()["source_freshness"]["wasp"]["last_activity"] == "2026-08-25T01:00:00Z"
    assert response.json()["source_freshness"]["website"]["checked_at"]


def test_summary_marks_failed_wasp_source_unavailable_instead_of_zero() -> None:
    client, repository = make_client()
    repository.events_between = lambda *_args: (_ for _ in ()).throw(ConnectionError())
    login(client)

    response = client.get("/api/v1/summary?period=30d")

    assert response.status_code == 200
    assert response.json()["sources"]["wasp"] == "unavailable"
    assert response.json()["kpis"]["successful_runs"] is None
    assert response.json()["kpis"]["success_rate"] is None
    assert response.json()["kpis"]["countries"] is None
    countries = client.get("/api/v1/wasp/countries?period=30d")
    assert countries.status_code == 200
    assert countries.json()["status"] == "unavailable"
    assert countries.json()["countries"] == []


def test_country_api_supports_map_detail_and_csv_export() -> None:
    client, repository = make_client()
    repository.seed_event("session_start", "s1", "AU", "2026-08-25T00:00:00Z")
    repository.seed_event("run_success", "s1", "AU", "2026-08-25T01:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")
    repository.seed_event("download", "s1", "AU", "2026-08-25T02:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")
    login(client)

    countries = client.get("/api/v1/wasp/countries?period=30d")
    detail = client.get("/api/v1/wasp/countries/AU?period=30d")
    export = client.get("/api/v1/wasp/export.csv?period=30d")

    assert countries.status_code == 200
    assert countries.json()["countries"][0]["country"] == "Australia"
    assert detail.json()["downloads"] == 1
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"] == 'attachment; filename="hydroclimatex_usage_30d.csv"'
    assert "Country Code,Country,Successful Runs,Failed Runs,Downloads,Sessions,Last Activity" in export.text
    assert "AU,Australia,1,0,1,1,2026-08-25T02:00:00Z" in export.text


def test_unknown_country_detail_returns_not_found() -> None:
    client, _ = make_client()
    login(client)

    assert client.get("/api/v1/wasp/countries/DE?period=30d").status_code == 404


def test_report_preview_and_send_are_protected_by_session_and_csrf() -> None:
    repository = MemoryRepository()
    reports = FakeReports(repository)
    settings = Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash=hash_password("correct horse battery staple"),
        internal_token="i" * 32,
        session_secret="s" * 32,
        collected_since=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    app = create_app(settings=settings, repository=repository, umami=FakeUmami(), report_service=reports)
    client = TestClient(app, base_url="https://analytics.hydroclimatex.test")

    assert client.get("/api/v1/reports/2026-07").status_code == 401
    csrf = login(client)
    preview = client.get("/api/v1/reports/2026-07")
    assert preview.status_code == 200
    assert preview.json()["status"] == "generated"
    assert client.post("/api/v1/reports/2026-07/send").status_code == 403
    sent = client.post(
        "/api/v1/reports/2026-07/send",
        json={"force": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert sent.json() == {"sent": True, "message_id": "message-id"}
    assert reports.sent == [(date(2026, 7, 1), True)]
