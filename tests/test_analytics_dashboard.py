from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from analytics_app.config import Settings
from analytics_app.main import create_app
from analytics_app.repository import MemoryRepository
from analytics_app.security import hash_password


ROOT = Path(__file__).resolve().parents[1]


class EmptyUmami:
    def summary(self, _period):
        return {"status": "unavailable", "visitors": None, "pageviews": None, "countries": None}

    def website_windows(self, _now):
        return {"status": "unavailable", "metrics": []}


class EmptyReports:
    pass


def test_dashboard_shell_is_served_without_exposing_data() -> None:
    settings = Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash=hash_password("correct horse battery staple"),
        internal_token="i" * 32,
        session_secret="s" * 32,
        collected_since=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    client = TestClient(
        create_app(settings=settings, repository=MemoryRepository(), umami=EmptyUmami(), report_service=EmptyReports()),
        base_url="https://analytics.hydroclimatex.test",
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "HydroClimateX Analytics" in response.text
    assert "Administrator sign in" in response.text
    assert "Global WASP Usage" in response.text
    assert "Website Analytics" in response.text
    assert "Monthly Reports" in response.text
    assert "1,245" not in response.text


def test_dashboard_script_uses_only_protected_analytics_interfaces() -> None:
    script = (ROOT / "analytics_app" / "static" / "app.js").read_text(encoding="utf-8")

    for endpoint in (
        "/auth/login", "/auth/logout", "/auth/session", "/api/v1/summary",
        "/api/v1/website/windows", "/api/v1/wasp/countries",
        "/api/v1/wasp/countries/", "/api/v1/wasp/export.csv",
        "/api/v1/reports/",
    ):
        assert endpoint in script
    assert "Plotly.newPlot" in script
    assert "plotly_click" in script


def test_dashboard_css_has_responsive_and_accessible_states() -> None:
    css = (ROOT / "analytics_app" / "static" / "style.css").read_text(encoding="utf-8")

    assert "@media (max-width: 760px)" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
