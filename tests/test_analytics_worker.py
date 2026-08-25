from datetime import date, datetime, timezone

from analytics_app.worker import REPORT_TIMEZONE, send_with_retries


class FlakyReports:
    def __init__(self):
        self.calls = 0

    def send(self, month):
        self.calls += 1
        if self.calls < 3:
            raise OSError("smtp unavailable")
        return {"sent": True, "month": month.isoformat()}


def test_worker_retries_without_changing_month():
    reports = FlakyReports()
    waits = []
    result = send_with_retries(
        reports,
        now=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
        attempts=3,
        sleep=waits.append,
    )
    assert result == {"sent": True, "month": "2026-08-01"}
    assert waits == [60, 300]


def test_report_schedule_is_hong_kong_first_day_at_eight():
    assert REPORT_TIMEZONE == "Asia/Hong_Kong"
