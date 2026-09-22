import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "analytics_app" / "migrations"


def migrations_sql() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))


def test_postgres_schema_contains_required_private_analytics_tables() -> None:
    schema = migrations_sql().lower()

    for table in ("usage_events", "admin_sessions", "admin_audit", "monthly_reports"):
        assert f"create table if not exists {table}" in schema
    assert "create unique index if not exists run_outcome_once" in schema
    assert "event_type in ('session_start', 'run_success', 'run_failure', 'download')" in schema


def test_usage_events_are_scoped_per_application() -> None:
    """WASP and LISFLOOD share one table, discriminated by app, and stay isolated."""
    schema = migrations_sql().lower()

    # WASP rows predate the app column and must keep their data as app = 'wasp'.
    assert "rename to usage_events" in schema
    assert "column if not exists app text not null default 'wasp'" in schema
    assert "app in ('wasp', 'lisflood')" in schema
    # LISFLOOD run ids are 20-character job ids, so run_id can no longer be a UUID.
    assert "alter column run_id type text" in schema
    for index in ("usage_events_exact_once", "run_outcome_once", "usage_events_period_country"):
        assert f"create unique index if not exists {index}" in schema or f"create index if not exists {index}" in schema
    assert "on usage_events (app, run_id)" in schema
    assert "on usage_events (app, occurred_at, country_code)" in schema


def test_postgres_schema_is_re_runnable() -> None:
    """migrate() re-executes every migration on each deploy; statements must be idempotent."""
    schema = migrations_sql()
    bare = re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX) (?!IF NOT EXISTS)\w+", schema)
    assert not bare, f"migration statements must be guarded by IF NOT EXISTS: {bare}"


def test_postgres_schema_does_not_persist_sensitive_request_data() -> None:
    schema = migrations_sql().lower()

    for forbidden in ("ip_address", "remote_addr", "filename", "upload_data", "prediction_data", "error_message"):
        assert forbidden not in schema
