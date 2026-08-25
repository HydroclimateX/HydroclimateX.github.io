from __future__ import annotations

import csv
import html
import io
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import plotly.graph_objects as go

from .config import Settings
from .domain import HONG_KONG, Period, aggregate_country_rows
from .repository import MonthlyReport, Repository


def previous_complete_month(now: datetime | None = None) -> date:
    local = (now or datetime.now(timezone.utc)).astimezone(HONG_KONG)
    first = local.date().replace(day=1)
    previous_last = first.fromordinal(first.toordinal() - 1)
    return previous_last.replace(day=1)


def month_period(report_month: date) -> Period:
    if report_month.day != 1:
        raise ValueError("report month must be the first day of a month")
    if report_month.month == 12:
        next_month = date(report_month.year + 1, 1, 1)
    else:
        next_month = date(report_month.year, report_month.month + 1, 1)
    start = datetime(report_month.year, report_month.month, 1, tzinfo=HONG_KONG)
    end = datetime(next_month.year, next_month.month, 1, tzinfo=HONG_KONG)
    return Period("month", report_month.strftime("%B %Y"), start, end)


def render_map_png(rows: list[dict[str, object]]) -> bytes:
    figure = go.Figure(go.Choropleth(
        locations=[row["country"] for row in rows if row["country"] != "Unknown"],
        locationmode="country names",
        z=[row["successful_runs"] for row in rows if row["country"] != "Unknown"],
        colorscale=[[0, "#e6f2ef"], [1, "#0f766e"]],
        colorbar_title="Successful runs",
        marker_line_color="#ffffff",
        marker_line_width=0.5,
    ))
    figure.update_layout(
        geo={"showframe": False, "showcoastlines": False, "projection_type": "natural earth"},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        width=1000,
        height=520,
    )
    return figure.to_image(format="png", engine="kaleido")


def send_test_email(settings: Settings, *, smtp_factory=smtplib.SMTP_SSL) -> None:
    message = EmailMessage()
    message["From"] = settings.report_from
    message["To"] = settings.report_to
    message["Subject"] = "HydroclimateX Analytics — SMTP test"
    message.set_content("HydroclimateX Analytics SMTP configuration test succeeded.")
    with smtp_factory(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


class ReportService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        umami,
        *,
        smtp_factory=smtplib.SMTP_SSL,
        map_renderer=render_map_png,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.umami = umami
        self.smtp_factory = smtp_factory
        self.map_renderer = map_renderer

    def generate(self, report_month: date) -> MonthlyReport:
        existing = self.repository.get_report(report_month)
        if existing:
            return existing
        period = month_period(report_month)
        website = self.umami.summary(period)
        try:
            usage = aggregate_country_rows(self.repository.events_between(period.start, period.end))
            usage["status"] = "available"
        except Exception:
            usage = {
                "status": "unavailable",
                "totals": {
                    "successful_runs": None,
                    "failed_runs": None,
                    "downloads": None,
                    "sessions": None,
                    "countries": None,
                    "success_rate": None,
                    "last_activity": None,
                },
                "countries": [],
            }
        snapshot = {
            "month": report_month.isoformat(),
            "label": period.label,
            "timezone": "Asia/Hong_Kong",
            "website": website,
            "wasp": usage,
        }
        report = MonthlyReport(report_month, snapshot, "generated", datetime.now(timezone.utc))
        return self.repository.save_report(report)

    def _csv_bytes(self, report: MonthlyReport) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "Country Code", "Country", "Successful Runs", "Failed Runs",
            "Downloads", "Sessions", "Last Activity",
        ])
        for row in report.snapshot["wasp"]["countries"]:  # type: ignore[index]
            writer.writerow([
                row["country_code"], row["country"], row["successful_runs"], row["failed_runs"],
                row["downloads"], row["sessions"], row["last_activity"],
            ])
        return output.getvalue().encode("utf-8")

    def _html(self, report: MonthlyReport) -> str:
        website = report.snapshot["website"]  # type: ignore[assignment]
        wasp = report.snapshot["wasp"]  # type: ignore[assignment]
        totals = wasp["totals"]
        website_available = website["status"] == "available"
        def display(value) -> str:
            return "Data unavailable" if value is None else f"{value:,}" if isinstance(value, int) else str(value)
        wasp_available = wasp.get("status", "available") == "available"
        success_rate = totals["success_rate"]
        top = wasp["countries"][:5]
        top_rows = "".join(
            f"<li>{html.escape(str(row['country']))} — {row['successful_runs']} successful runs</li>"
            for row in top
        ) or "<li>No verified WASP usage in this period</li>"
        return f"""
        <html><body style="font-family:Arial,sans-serif;color:#17313a">
        <h1>HydroClimateX Monthly Analytics</h1><h2>{html.escape(str(report.snapshot['label']))}</h2>
        <h3>WEBSITE</h3>
        <p>{'Data unavailable' if not website_available else ''}</p>
        <table><tr><td>Visitors</td><td>{display(website.get('visitors'))}</td></tr>
        <tr><td>Page views</td><td>{display(website.get('pageviews'))}</td></tr>
        <tr><td>Countries</td><td>{display(website.get('countries'))}</td></tr>
        <tr><td>WASP launches</td><td>{display(website.get('wasp_launches'))}</td></tr>
        <tr><td>GitHub clicks</td><td>{display(website.get('github_clicks'))}</td></tr>
        <tr><td>Publication clicks</td><td>{display(website.get('publication_clicks'))}</td></tr>
        <tr><td>File downloads</td><td>{display(website.get('file_downloads'))}</td></tr></table>
        <h3>WASP</h3><p>{'' if wasp_available else 'WASP data unavailable'}</p><table>
        <tr><td>Successful runs</td><td>{display(totals['successful_runs'])}</td></tr>
        <tr><td>Failed runs</td><td>{display(totals['failed_runs'])}</td></tr>
        <tr><td>Success rate</td><td>{'N/A' if success_rate is None else f'{success_rate:.1%}'}</td></tr>
        <tr><td>Countries</td><td>{display(totals['countries'])}</td></tr>
        <tr><td>Result downloads</td><td>{display(totals['downloads'])}</td></tr></table>
        <h3>Top countries</h3><ol>{top_rows}</ol>
        {('<img src="cid:usage-map" alt="Global WASP usage map" style="max-width:100%">' if wasp_available else '')}
        <p style="font-size:12px;color:#60747a"><a href="https://db-ip.com">IP Geolocation by DB-IP</a></p>
        </body></html>
        """

    def _message(self, report: MonthlyReport) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.settings.report_from
        message["To"] = self.settings.report_to
        message["Subject"] = f"HydroClimateX Monthly Analytics — {report.snapshot['label']}"
        message["Message-ID"] = make_msgid(domain="hydroclimatex.com")
        message.set_content(
            f"HydroClimateX Monthly Analytics — {report.snapshot['label']}\n\n"
            "IP Geolocation by DB-IP: https://db-ip.com"
        )
        message.add_alternative(self._html(report), subtype="html")
        wasp = report.snapshot["wasp"]  # type: ignore[assignment]
        if wasp.get("status", "available") == "available":
            html_part = message.get_payload()[-1]
            map_bytes = self.map_renderer(wasp["countries"])
            html_part.add_related(map_bytes, maintype="image", subtype="png", cid="<usage-map>", filename="global-wasp-usage.png")
            filename = f"hydroclimatex_usage_{report.report_month:%Y-%m}.csv"
            message.add_attachment(self._csv_bytes(report), maintype="text", subtype="csv", filename=filename)
        return message

    def send(self, report_month: date, *, force: bool = False) -> dict[str, object]:
        existing = self.repository.get_report(report_month)
        if existing and existing.status == "sent" and not force:
            return {"sent": False, "reason": "already sent"}
        report = existing or self.generate(report_month)
        if not self.repository.claim_report_delivery(report_month, force=force):
            current = self.repository.get_report(report_month)
            reason = "already sent" if current and current.status == "sent" else "delivery in progress"
            return {"sent": False, "reason": reason}
        report = self.repository.get_report(report_month) or report
        try:
            message = self._message(report)
            with self.smtp_factory(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
        except Exception as exc:
            failed = MonthlyReport(
                report.report_month, report.snapshot, "failed", report.generated_at,
                failure_code=type(exc).__name__,
            )
            self.repository.save_report(failed)
            raise
        sent = MonthlyReport(
            report.report_month, report.snapshot, "sent", report.generated_at,
            sent_at=datetime.now(timezone.utc), message_id=str(message["Message-ID"]),
        )
        self.repository.save_report(sent)
        return {"sent": True, "message_id": sent.message_id}
