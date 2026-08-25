from datetime import datetime, timezone

import pytest

from analytics_app.domain import (
    PeriodError,
    aggregate_country_rows,
    resolve_period,
)


def test_last_30_days_uses_hong_kong_calendar_date() -> None:
    now = datetime(2026, 8, 25, 1, 30, tzinfo=timezone.utc)

    period = resolve_period("30d", now=now)

    assert period.label == "Last 30 days"
    assert period.start.isoformat() == "2026-07-27T00:00:00+08:00"
    assert period.end.isoformat() == "2026-08-26T00:00:00+08:00"


def test_custom_period_rejects_reversed_dates() -> None:
    with pytest.raises(PeriodError, match="start date must not be after end date"):
        resolve_period("custom", start="2026-08-10", end="2026-08-01")


def test_country_aggregation_calculates_totals_and_success_rate() -> None:
    rows = [
        {"country_code": "AU", "event_type": "session_start", "session_hash": "s1", "occurred_at": "2026-08-01T00:00:00Z"},
        {"country_code": "AU", "event_type": "run_success", "session_hash": "s1", "occurred_at": "2026-08-01T01:00:00Z"},
        {"country_code": "AU", "event_type": "run_failure", "session_hash": "s1", "occurred_at": "2026-08-01T02:00:00Z"},
        {"country_code": "AU", "event_type": "download", "session_hash": "s1", "occurred_at": "2026-08-01T03:00:00Z"},
        {"country_code": "CN", "event_type": "run_success", "session_hash": "s2", "occurred_at": "2026-08-02T01:00:00Z"},
    ]

    result = aggregate_country_rows(rows)

    assert result["totals"] == {
        "successful_runs": 2,
        "failed_runs": 1,
        "downloads": 1,
        "sessions": 2,
        "countries": 2,
        "success_rate": pytest.approx(2 / 3),
    }
    assert result["countries"][0] == {
        "country_code": "AU",
        "country": "Australia",
        "country_iso3": "AUS",
        "successful_runs": 1,
        "failed_runs": 1,
        "downloads": 1,
        "sessions": 1,
        "last_activity": "2026-08-01T03:00:00Z",
    }


def test_country_aggregation_treats_missing_country_as_unknown() -> None:
    result = aggregate_country_rows([
        {"country_code": None, "event_type": "session_start", "session_hash": "s1", "occurred_at": "2026-08-01T00:00:00Z"},
    ])

    assert result["countries"][0]["country_code"] == "ZZ"
    assert result["countries"][0]["country"] == "Unknown"
    assert result["countries"][0]["country_iso3"] is None


def test_country_aggregation_emits_iso3_for_micro_territories() -> None:
    result = aggregate_country_rows([
        {"country_code": "HK", "event_type": "run_success", "session_hash": "s1", "occurred_at": "2026-08-01T01:00:00Z"},
        {"country_code": "SG", "event_type": "run_success", "session_hash": "s2", "occurred_at": "2026-08-01T02:00:00Z"},
    ])

    by_code = {row["country_code"]: row for row in result["countries"]}
    assert by_code["HK"]["country"] == "Hong Kong"
    assert by_code["HK"]["country_iso3"] == "HKG"
    assert by_code["SG"]["country_iso3"] == "SGP"
