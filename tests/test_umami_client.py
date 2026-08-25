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
