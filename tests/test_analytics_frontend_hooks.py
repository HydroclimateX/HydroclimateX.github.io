from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_pages_load_privacy_first_telemetry_bootstrap() -> None:
    homepage = read("index.html")
    showcase = read("showcase/wasp-web/index.html")
    tracker = read("analytics.js")

    assert 'src="/analytics.js"' in homepage
    assert 'src="/analytics.js"' in showcase
    assert "https://telemetry.hydroclimatex.com/config.json" in tracker
    assert "data-domains" in tracker
    for event in ("wasp_launch", "publication_click", "github_click", "file_download"):
        assert event in tracker
    assert "email" not in tracker.lower()


def test_pages_workflow_publishes_analytics_bootstrap() -> None:
    workflow = read(".github/workflows/static.yml")

    assert "analytics.js" in workflow


def test_wasp_frontend_starts_session_and_records_result_download() -> None:
    app = read("wasp-app/index.html")

    assert "'/api/usage/session'" in app
    assert "'/api/usage/download'" in app
    assert "lastResult.analytics_run_id" in app
    assert "keepalive: true" in app
