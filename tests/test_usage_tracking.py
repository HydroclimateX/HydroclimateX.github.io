from datetime import datetime, timezone

from backend.usage import UsageTracker, country_from_ip, new_session_token, session_hash


def test_session_hash_is_stable_and_does_not_reveal_cookie() -> None:
    raw = new_session_token()

    digest = session_hash(raw, "secret-with-at-least-32-characters")

    assert len(digest) == 64
    assert raw not in digest
    assert digest == session_hash(raw, "secret-with-at-least-32-characters")


def test_country_lookup_returns_only_iso_code() -> None:
    class FakeReader:
        def get(self, _ip):
            return {"country": {"iso_code": "au"}}

    assert country_from_ip("203.0.113.5", reader=FakeReader()) == "AU"
    assert country_from_ip("not-an-ip", reader=FakeReader()) == "ZZ"


def test_country_lookup_fails_closed_for_missing_or_malformed_dbip_records() -> None:
    class MissingReader:
        def get(self, _ip):
            return None

    class MalformedReader:
        def get(self, _ip):
            return {"country": {"iso_code": "1A"}}

    assert country_from_ip("203.0.113.5", reader=MissingReader()) == "ZZ"
    assert country_from_ip("203.0.113.5", reader=MalformedReader()) == "ZZ"


def test_tracker_sends_only_privacy_safe_event_fields() -> None:
    captured = []
    tracker = UsageTracker(
        endpoint="http://analytics-api:8001/internal/v1/wasp-events",
        internal_token="token",
        session_secret="secret-with-at-least-32-characters",
        sender=lambda endpoint, token, payload: captured.append((endpoint, token, payload)),
    )

    assert tracker.emit(
        "run_success",
        session_token="browser-cookie",
        country_code="AU",
        run_id="267eb25f-e2e5-4654-bf41-2bdcfbdedddc",
        occurred_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    ) is True

    payload = captured[0][2]
    assert set(payload) == {"event_type", "session_hash", "country_code", "occurred_at", "run_id"}
    assert "browser-cookie" not in str(payload)
    assert "ip" not in " ".join(payload)


def test_tracker_fails_open_when_analytics_is_unavailable() -> None:
    def offline(*_args):
        raise OSError("offline")

    tracker = UsageTracker(
        endpoint="http://analytics-api:8001/internal/v1/wasp-events",
        internal_token="token",
        session_secret="secret-with-at-least-32-characters",
        sender=offline,
    )

    assert tracker.emit(
        "session_start",
        session_token="browser-cookie",
        country_code="ZZ",
        run_id=None,
    ) is False
