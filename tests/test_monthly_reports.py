from datetime import date, datetime, timezone

from analytics_app.config import Settings
from analytics_app.reports import ReportService, previous_complete_month, send_test_email
from analytics_app.repository import MemoryRepository, MonthlyReport


class FakeUmami:
    def summary(self, _period):
        return {
            "status": "available",
            "visitors": 1245,
            "pageviews": 3682,
            "countries": 46,
            "wasp_launches": 382,
            "publication_clicks": 126,
            "github_clicks": 174,
            "file_downloads": 91,
        }


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = None
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.messages.append(message)


def settings() -> Settings:
    return Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash="$argon2id$unused",
        internal_token="i" * 32,
        session_secret="s" * 32,
        collected_since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        smtp_password="smtp-authorization-code",
    )


def test_previous_complete_month_uses_hong_kong_time() -> None:
    now = datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc)

    assert previous_complete_month(now) == date(2026, 7, 1)


def test_monthly_email_contains_html_inline_map_and_csv_attachment() -> None:
    FakeSMTP.instances.clear()
    repository = MemoryRepository()
    repository.seed_event("session_start", "s1", "AU", "2026-07-05T00:00:00Z")
    repository.seed_event("run_success", "s1", "AU", "2026-07-05T01:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")
    service = ReportService(
        settings(), repository, FakeUmami(),
        smtp_factory=FakeSMTP,
        map_renderer=lambda _rows: b"png-bytes",
    )

    result = service.send(date(2026, 7, 1))

    assert result["sent"] is True
    smtp = FakeSMTP.instances[0]
    assert smtp.logged_in == ("zejiang_hydrology@126.com", "smtp-authorization-code")
    message = smtp.messages[0]
    assert message["From"] == "zejiang_hydrology@126.com"
    assert message["To"] == "ze.jiang@hhu.edu.cn"
    assert message["Subject"] == "HydroclimateX Monthly Analytics — July 2026"
    payload_types = [part.get_content_type() for part in message.walk()]
    assert "text/html" in payload_types
    assert "image/png" in payload_types
    assert "text/csv" in payload_types
    assert "Successful runs" in message.as_string()
    report_bodies = "\n".join(
        part.get_content()
        for part in message.walk()
        if part.get_content_type() in {"text/plain", "text/html"}
    )
    assert "IP Geolocation by DB-IP" in report_bodies
    assert "https://db-ip.com" in report_bodies
    assert repository.get_report(date(2026, 7, 1)).status == "sent"


def test_sent_month_is_idempotent() -> None:
    FakeSMTP.instances.clear()
    repository = MemoryRepository()
    service = ReportService(
        settings(), repository, FakeUmami(),
        smtp_factory=FakeSMTP,
        map_renderer=lambda _rows: b"png-bytes",
    )

    first = service.send(date(2026, 7, 1))
    second = service.send(date(2026, 7, 1))

    assert first["sent"] is True
    assert second == {"sent": False, "reason": "already sent"}
    assert len(FakeSMTP.instances) == 1


def test_unavailable_wasp_source_sends_status_without_unverified_attachments() -> None:
    class BrokenRepository(MemoryRepository):
        def events_between(self, _start, _end):
            raise ConnectionError("database unavailable")

    FakeSMTP.instances.clear()
    service = ReportService(
        settings(), BrokenRepository(), FakeUmami(),
        smtp_factory=FakeSMTP,
        map_renderer=lambda _rows: (_ for _ in ()).throw(AssertionError("map must not be rendered")),
    )

    result = service.send(date(2026, 7, 1))

    assert result["sent"] is True
    message = FakeSMTP.instances[0].messages[0]
    assert "WASP data unavailable" in message.as_string()
    payload_types = [part.get_content_type() for part in message.walk()]
    assert "image/png" not in payload_types
    assert "text/csv" not in payload_types


def test_map_render_failure_does_not_abort_send() -> None:
    FakeSMTP.instances.clear()
    repository = MemoryRepository()
    repository.seed_event("session_start", "s1", "AU", "2026-07-05T00:00:00Z")
    repository.seed_event("run_success", "s1", "AU", "2026-07-05T01:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")

    def broken_map(_rows):
        raise RuntimeError("kaleido failed")

    service = ReportService(
        settings(), repository, FakeUmami(),
        smtp_factory=FakeSMTP,
        map_renderer=broken_map,
    )

    result = service.send(date(2026, 7, 1))

    assert result["sent"] is True
    message = FakeSMTP.instances[0].messages[0]
    payload_types = [part.get_content_type() for part in message.walk()]
    assert "text/html" in payload_types
    assert "text/csv" in payload_types
    assert "image/png" not in payload_types
    body = message.as_string()
    assert "Visitors" in body
    assert "Successful runs" in body
    saved = repository.get_report(date(2026, 7, 1))
    assert saved.status == "sent"
    assert saved.failure_code is None


def test_report_includes_website_section_when_available() -> None:
    FakeSMTP.instances.clear()
    repository = MemoryRepository()
    repository.seed_event("session_start", "s1", "AU", "2026-07-05T00:00:00Z")
    repository.seed_event("run_success", "s1", "AU", "2026-07-05T01:00:00Z", run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc")
    service = ReportService(
        settings(), repository, FakeUmami(),
        smtp_factory=FakeSMTP,
        map_renderer=lambda _rows: b"png-bytes",
    )

    result = service.send(date(2026, 7, 1))

    assert result["sent"] is True
    message = FakeSMTP.instances[0].messages[0]
    html = "\n".join(
        part.get_content()
        for part in message.walk()
        if part.get_content_type() == "text/html"
    )
    assert "WEBSITE" in html
    assert "Visitors" in html
    assert "1,245" in html
    assert "WASP" in html
    assert "Successful runs" in html


def test_smtp_test_uses_configured_sender_and_recipient() -> None:
    FakeSMTP.instances.clear()

    send_test_email(settings(), smtp_factory=FakeSMTP)

    message = FakeSMTP.instances[0].messages[0]
    assert message["From"] == "zejiang_hydrology@126.com"
    assert message["To"] == "ze.jiang@hhu.edu.cn"
    assert message["Subject"] == "HydroclimateX Analytics — SMTP test"
    assert "configuration test" in message.get_content()


def test_in_progress_month_does_not_send_a_duplicate_message() -> None:
    FakeSMTP.instances.clear()
    repository = MemoryRepository()
    generated = ReportService(settings(), repository, FakeUmami(), smtp_factory=FakeSMTP).generate(date(2026, 7, 1))
    repository.save_report(MonthlyReport(generated.report_month, generated.snapshot, "sending", generated.generated_at))
    service = ReportService(settings(), repository, FakeUmami(), smtp_factory=FakeSMTP)

    assert service.send(date(2026, 7, 1)) == {"sent": False, "reason": "delivery in progress"}
    assert FakeSMTP.instances == []


def test_generate_regenerates_when_cached_website_unavailable() -> None:
    repository = MemoryRepository()
    service = ReportService(settings(), repository, FakeUmami(), smtp_factory=FakeSMTP, map_renderer=lambda _rows: b"png")
    repository.save_report(MonthlyReport(
        date(2026, 7, 1),
        {"month": "2026-07-01", "label": "July 2026", "timezone": "Asia/Hong_Kong",
         "website": {"status": "unavailable", "visitors": None, "pageviews": None, "countries": None},
         "wasp": {"status": "unavailable", "totals": {}, "countries": []}},
        "generated", datetime.now(timezone.utc),
    ))

    report = service.generate(date(2026, 7, 1))

    assert report.snapshot["website"]["status"] == "available"
    assert report.snapshot["website"]["visitors"] == 1245


def test_save_report_freezes_snapshot_only_after_sent() -> None:
    repository = MemoryRepository()
    first = {"website": {"status": "available", "visitors": 10}, "wasp": {}}
    repository.save_report(MonthlyReport(date(2026, 7, 1), first, "generated", datetime.now(timezone.utc)))
    second = {"website": {"status": "available", "visitors": 99}, "wasp": {}}
    repository.save_report(MonthlyReport(date(2026, 7, 1), second, "generated", datetime.now(timezone.utc)))
    assert repository.get_report(date(2026, 7, 1)).snapshot["website"]["visitors"] == 99
    repository.save_report(MonthlyReport(date(2026, 7, 1), second, "sent", datetime.now(timezone.utc)))
    third = {"website": {"status": "available", "visitors": 123}, "wasp": {}}
    repository.save_report(MonthlyReport(date(2026, 7, 1), third, "sent", datetime.now(timezone.utc)))
    assert repository.get_report(date(2026, 7, 1)).snapshot["website"]["visitors"] == 99
