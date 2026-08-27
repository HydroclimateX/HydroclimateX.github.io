import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_postgres_schema_contains_required_private_analytics_tables() -> None:
    schema = (ROOT / "analytics_app" / "migrations" / "001_initial.sql").read_text().lower()

    for table in ("wasp_events", "admin_sessions", "admin_audit", "monthly_reports"):
        assert f"create table if not exists {table}" in schema
    assert "create unique index if not exists run_outcome_once" in schema
    assert "event_type in ('session_start', 'run_success', 'run_failure', 'download')" in schema


def test_postgres_schema_is_re_runnable() -> None:
    """migrate() re-executes the schema on every deploy; every statement must be idempotent."""
    schema = (ROOT / "analytics_app" / "migrations" / "001_initial.sql").read_text()
    bare = re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX) (?!IF NOT EXISTS)\w+", schema)
    assert not bare, f"migration statements must be guarded by IF NOT EXISTS: {bare}"


def test_postgres_schema_does_not_persist_sensitive_request_data() -> None:
    schema = (ROOT / "analytics_app" / "migrations" / "001_initial.sql").read_text().lower()

    for forbidden in ("ip_address", "remote_addr", "filename", "upload_data", "prediction_data", "error_message"):
        assert forbidden not in schema
