from __future__ import annotations

from datetime import datetime

import httpx

from .config import Settings
from .domain import NORMALIZE_COUNTRY, Period, resolve_period


EVENT_METRICS = {
    "wasp_launch": "wasp_launches",
    "publication_click": "publication_clicks",
    "github_click": "github_clicks",
    "file_download": "file_downloads",
}


class UmamiClient:
    def __init__(self, settings: Settings, *, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http_client or httpx.Client(base_url=settings.umami_base_url, timeout=8.0)
        self._token: str | None = None

    def _authenticate(self) -> str:
        if self._token:
            return self._token
        response = self.http.post(
            f"{self.settings.umami_base_url}/api/auth/login",
            json={"username": self.settings.umami_username, "password": self.settings.umami_password},
        )
        response.raise_for_status()
        self._token = response.json()["token"]
        return self._token

    def _get(self, path: str, params: dict[str, object]) -> object:
        token = self._authenticate()
        response = self.http.get(
            f"{self.settings.umami_base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            self._token = None
            token = self._authenticate()
            response = self.http.get(
                f"{self.settings.umami_base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _empty(status: str) -> dict[str, object]:
        return {
            "status": status,
            "visitors": None,
            "pageviews": None,
            "countries": None,
            "wasp_launches": None,
            "publication_clicks": None,
            "github_clicks": None,
            "file_downloads": None,
        }

    def summary(self, period: Period) -> dict[str, object]:
        if not all((self.settings.umami_username, self.settings.umami_password, self.settings.umami_website_id)):
            return self._empty("configuration missing")
        params = {
            "startAt": int(period.start.timestamp() * 1000),
            "endAt": int(period.end.timestamp() * 1000),
        }
        base = f"/api/websites/{self.settings.umami_website_id}"
        try:
            stats = self._get(f"{base}/stats", params)
            countries = self._get(f"{base}/metrics", {**params, "type": "country", "limit": 500})
            events = self._get(f"{base}/metrics", {**params, "type": "event", "limit": 500})
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return self._empty("unavailable")

        event_counts = {str(row.get("x")): int(row.get("y", 0)) for row in events}  # type: ignore[union-attr]
        merged_countries: dict[str, int] = {}
        for row in countries:  # type: ignore[union-attr]
            code = NORMALIZE_COUNTRY.get(str(row.get("x", "")), str(row.get("x", "")))
            merged_countries[code] = merged_countries.get(code, 0) + int(row.get("y", 0))
        result = {
            "status": "available",
            "visitors": int(stats.get("visitors", 0)),  # type: ignore[union-attr]
            "pageviews": int(stats.get("pageviews", 0)),  # type: ignore[union-attr]
            "countries": len(merged_countries),
        }
        result.update({field: event_counts.get(event, 0) for event, field in EVENT_METRICS.items()})
        return result

    def website_windows(self, now: datetime) -> dict[str, object]:
        periods = {
            "days_30": resolve_period("30d", now=now),
            "months_12": resolve_period("12m", now=now),
            "all_time": resolve_period("all", now=now, collected_since=self.settings.collected_since),
        }
        summaries = {name: self.summary(period) for name, period in periods.items()}
        available = all(item["status"] == "available" for item in summaries.values())
        fields = [
            ("Visitors", "visitors"),
            ("Page views", "pageviews"),
            ("Countries", "countries"),
            ("WASP launches", "wasp_launches"),
            ("Publication clicks", "publication_clicks"),
            ("GitHub clicks", "github_clicks"),
            ("File downloads", "file_downloads"),
        ]
        return {
            "status": "available" if available else "unavailable",
            "metrics": [
                {
                    "metric": label,
                    **{window: summaries[window].get(field) for window in periods},
                }
                for label, field in fields
            ],
        }
