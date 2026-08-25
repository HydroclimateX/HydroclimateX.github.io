from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import Settings
from .reports import ReportService, previous_complete_month
from .repository_postgres import PostgresRepository
from .umami import UmamiClient


REPORT_TIMEZONE = "Asia/Hong_Kong"
LOG = logging.getLogger("hydroclimatex.analytics.worker")


def send_with_retries(
    reports: ReportService,
    *,
    now: datetime | None = None,
    attempts: int = 3,
    sleep=time.sleep,
) -> dict[str, object]:
    month = previous_complete_month(now or datetime.now(timezone.utc))
    delays = (60, 300)
    for attempt in range(attempts):
        try:
            return reports.send(month)
        except Exception as exc:
            LOG.warning("monthly report delivery failed code=%s attempt=%d", type(exc).__name__, attempt + 1)
            if attempt + 1 == attempts:
                raise
            sleep(delays[min(attempt, len(delays) - 1)])
    raise RuntimeError("unreachable")


def build_report_service() -> ReportService:
    settings = Settings.from_env()
    repository = PostgresRepository(settings.database_url)
    return ReportService(settings, repository, UmamiClient(settings))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    reports = build_report_service()
    scheduler = BlockingScheduler(timezone=REPORT_TIMEZONE)
    scheduler.add_job(
        lambda: send_with_retries(reports),
        "cron",
        day=1,
        hour=8,
        minute=0,
        id="monthly-analytics-report",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
