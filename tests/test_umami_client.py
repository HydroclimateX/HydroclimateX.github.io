from datetime import datetime, timezone

import httpx

from analytics_app.config import Settings
from analytics_app.domain import resolve_period
from analytics_app.umami import UmamiClient


def settings() -> Settings:
    return Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash="$argon2id$unused",
        internal_token="i" * 32,
        session_secret="s" * 32,
        collected_since=datetime(2026, 8, 1, tzinfo=timezone.utc),
        umami_base_url="http://umami:3000",
        umami_username="dashboard-reader",
        umami_password="secret",
        umami_website_id="website-id",
    )


def test_summary_reads_stats_country_and_named_event_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "bearer-token"})
        assert request.headers["authorization"] == "Bearer bearer-token"
        if request.url.path.endswith("/stats"):
            return httpx.Response(200, json={"visitors": 1245, "pageviews": 3682})
        if request.url.params.get("type") == "country":
            return httpx.Response(200, json=[{"x": "AU", "y": 10}, {"x": "CN", "y": 3}])
        if request.url.params.get("type") == "event":
            return httpx.Response(200, json=[
                {"x": "wasp_launch", "y": 8},
                {"x": "github_click", "y": 4},
                {"x": "publication_click", "y": 3},
                {"x": "file_download", "y": 1},
            ])
        raise AssertionError(request.url)

    client = UmamiClient(settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    period = resolve_period("30d", now=datetime(2026, 8, 25, tzinfo=timezone.utc))

    result = client.summary(period)

    assert result == {
        "status": "available",
        "visitors": 1245,
        "pageviews": 3682,
        "countries": 2,
        "wasp_launches": 8,
        "publication_clicks": 3,
        "github_clicks": 4,
        "file_downloads": 1,
    }


def test_summary_marks_source_unavailable_instead_of_returning_zero() -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = UmamiClient(settings(), http_client=httpx.Client(transport=httpx.MockTransport(unavailable)))
    period = resolve_period("30d", now=datetime(2026, 8, 25, tzinfo=timezone.utc))

    result = client.summary(period)

    assert result["status"] == "unavailable"
    assert result["visitors"] is None
    assert result["pageviews"] is None


def test_missing_read_credentials_are_deferred_and_do_not_call_umami(monkeypatch) -> None:
    required = {
        "ANALYTICS_ADMIN_PASSWORD_HASH": "$argon2id$unused",
        "ANALYTICS_INTERNAL_TOKEN": "i" * 32,
        "ANALYTICS_SESSION_SECRET": "s" * 32,
        "ANALYTICS_DATABASE_URL": "postgresql://analytics:test@database/analytics",
        "ANALYTICS_SMTP_AUTHORIZATION_CODE": "smtp-code",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    for name in ("UMAMI_API_USERNAME", "UMAMI_API_PASSWORD", "UMAMI_WEBSITE_ID"):
        monkeypatch.delenv(name, raising=False)

    deferred = Settings.from_env()

    assert deferred.umami_username == ""
    assert deferred.umami_password == ""
    assert deferred.umami_website_id == ""

    def must_not_call_umami(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Umami must not be called before read credentials are configured")

    client = UmamiClient(
        deferred,
        http_client=httpx.Client(transport=httpx.MockTransport(must_not_call_umami)),
    )
    period = resolve_period("30d", now=datetime(2026, 8, 25, tzinfo=timezone.utc))

    assert client.summary(period)["status"] == "configuration missing"


def test_summary_counts_hk_tw_mo_under_china() -> None:
    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "bearer-token"})
        if request.url.path.endswith("/stats"):
            return httpx.Response(200, json={"visitors": 100, "pageviews": 300})
        if request.url.params.get("type") == "country":
            return httpx.Response(200, json=[
                {"x": "CN", "y": 10}, {"x": "HK", "y": 5}, {"x": "TW", "y": 3}, {"x": "MO", "y": 2},
            ])
        if request.url.params.get("type") == "event":
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    client = UmamiClient(settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    period = resolve_period("30d", now=datetime(2026, 8, 25, tzinfo=timezone.utc))

    result = client.summary(period)

    assert result["status"] == "available"
    assert result["countries"] == 1
