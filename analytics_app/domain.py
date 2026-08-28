from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


HONG_KONG = ZoneInfo("Asia/Hong_Kong")


class PeriodError(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: datetime
    end: datetime


def _local_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=HONG_KONG)


def resolve_period(
    key: str,
    *,
    now: datetime | None = None,
    start: str | None = None,
    end: str | None = None,
    collected_since: datetime | None = None,
) -> Period:
    current = (now or datetime.now(timezone.utc)).astimezone(HONG_KONG)
    tomorrow = _local_midnight(current.date() + timedelta(days=1))

    if key in {"7d", "30d"}:
        days = int(key[:-1])
        return Period(key, f"Last {days} days", tomorrow - timedelta(days=days), tomorrow)
    if key == "12m":
        year = current.year - (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        end_at = datetime(year, month, 1, tzinfo=HONG_KONG)
        return Period(key, "Last 12 months", datetime(end_at.year - 1, end_at.month, 1, tzinfo=HONG_KONG), end_at)
    if key == "all":
        if collected_since is None:
            raise PeriodError("collected_since is required for all-time periods")
        return Period(key, "All time", collected_since.astimezone(HONG_KONG), tomorrow)
    if key == "custom":
        if not start or not end:
            raise PeriodError("start and end dates are required")
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise PeriodError("dates must use YYYY-MM-DD") from exc
        if start_date > end_date:
            raise PeriodError("start date must not be after end date")
        return Period(key, "Custom", _local_midnight(start_date), _local_midnight(end_date + timedelta(days=1)))
    raise PeriodError(f"unsupported period: {key}")


NORMALIZE_COUNTRY = {"HK": "CN", "TW": "CN", "MO": "CN"}


COUNTRY_NAMES = {
    "AU": "Australia",
    "BR": "Brazil",
    "CA": "Canada",
    "CN": "China",
    "DE": "Germany",
    "FR": "France",
    "GB": "United Kingdom",
    "IN": "India",
    "JP": "Japan",
    "NZ": "New Zealand",
    "SG": "Singapore",
    "US": "United States",
    "ZZ": "Unknown",
}


def country_name(code: str) -> str:
    normalized = code.upper() if len(code) == 2 else "ZZ"
    if normalized in COUNTRY_NAMES:
        return COUNTRY_NAMES[normalized]
    try:
        import pycountry
        country = pycountry.countries.get(alpha_2=normalized)
        return country.name if country else normalized
    except ImportError:
        return normalized


def country_iso3(code: str) -> str | None:
    """ISO 3166-1 alpha-3 code for the map's GeoJSON featureidkey."""
    normalized = code.upper() if len(code) == 2 else "ZZ"
    if normalized == "ZZ":
        return None
    try:
        import pycountry
        country = pycountry.countries.get(alpha_2=normalized)
        return country.alpha_3 if country else None
    except ImportError:
        return None


def format_timestamp_seconds(value) -> str:
    """UTC ISO-8601 timestamp at second resolution for display (no fractional seconds)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value or "")


def aggregate_country_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    grouped: dict[str, dict[str, object]] = {}
    all_sessions: set[str] = set()

    for row in rows:
        raw_code = row.get("country_code")
        code = str(raw_code).upper() if raw_code and len(str(raw_code)) == 2 else "ZZ"
        code = NORMALIZE_COUNTRY.get(code, code)
        bucket = grouped.setdefault(code, {
            "country_code": code,
            "country": country_name(code),
            "country_iso3": country_iso3(code),
            "successful_runs": 0,
            "failed_runs": 0,
            "downloads": 0,
            "_sessions": set(),
            "last_activity": None,
        })
        event_type = row.get("event_type")
        if event_type == "run_success":
            bucket["successful_runs"] = int(bucket["successful_runs"]) + 1
        elif event_type == "run_failure":
            bucket["failed_runs"] = int(bucket["failed_runs"]) + 1
        elif event_type == "download":
            bucket["downloads"] = int(bucket["downloads"]) + 1

        session = row.get("session_hash")
        if session:
            session_key = str(session)
            bucket["_sessions"].add(session_key)  # type: ignore[union-attr]
            all_sessions.add(session_key)
        occurred_at = format_timestamp_seconds(row.get("occurred_at"))
        if occurred_at and (bucket["last_activity"] is None or occurred_at > str(bucket["last_activity"])):
            bucket["last_activity"] = occurred_at

    countries = []
    for bucket in grouped.values():
        sessions = len(bucket.pop("_sessions"))  # type: ignore[arg-type]
        bucket["sessions"] = sessions
        countries.append(bucket)
    countries.sort(key=lambda item: (-int(item["successful_runs"]), -int(item["sessions"]), str(item["country"])))

    successful = sum(int(row["successful_runs"]) for row in countries)
    failed = sum(int(row["failed_runs"]) for row in countries)
    total_runs = successful + failed
    return {
        "totals": {
            "successful_runs": successful,
            "failed_runs": failed,
            "downloads": sum(int(row["downloads"]) for row in countries),
            "sessions": len(all_sessions),
            "countries": len(countries),
            "success_rate": successful / total_runs if total_runs else None,
        },
        "countries": countries,
    }
