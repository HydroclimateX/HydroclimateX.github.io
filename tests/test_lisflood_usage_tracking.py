import contextlib
import http.client
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from lisflood_runner.service import Service, make_handler
from lisflood_runner.usage import (
    UsageTracker,
    country_from_ip,
    new_session_token,
    session_hash,
)


SECRET = "secret-with-at-least-32-characters"
ENDPOINT = "http://analytics-api:8001/internal/v1/lisflood-events"
EVENT_FIELDS = {"event_type", "session_hash", "country_code", "occurred_at", "run_id"}

HEADER = {
    "ncols": 1498.0,
    "nrows": 825.0,
    "xllcorner": 500000.0,
    "yllcorner": 3500000.0,
    "cellsize": 30.0,
}
LAYER_NAMES = ("dem", "population", "depth", "velocity", "hazard", "risk")
PROJECTED = [(118.0, 31.0), (118.0, 31.1), (118.2, 31.0), (118.2, 31.1)]
SNAPPED = ((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]])
RUN_BODY = json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20})


class FakeTracker:
    """Stand-in for :class:`UsageTracker`, mirroring the WASP API tests."""

    def __init__(self):
        self.events = []
        self.ips = []

    def country(self, client_ip):
        self.ips.append(client_ip)
        return "AU"

    def emit(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))
        return True


class RecordingSender:
    """Collect delivered payloads from the tracker's worker thread."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, endpoint, token, payload):
        self.calls.append((endpoint, token, payload))
        if self.error is not None:
            raise self.error

    def wait(self, count, timeout=2.0):
        deadline = time.monotonic() + timeout
        while len(self.calls) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return list(self.calls)


class FakeResponse:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def write_manifest(staging: Path) -> dict:
    layers = {name: f"{name}.png" for name in LAYER_NAMES}
    manifest = {
        "schemaVersion": 1,
        "generatedAt": "2026-01-01T00:00:00+00:00",
        "modelVersion": "model",
        "dataVersion": "data",
        "returnPeriod": 20,
        "rainfallMm": 10.0,
        "bounds": [[1.0, 2.0], [3.0, 4.0]],
        "populationBreaks": [0.0, 1.0, 2.0],
        "layers": layers,
        "stats": {
            "floodedAreaKm2": 1.0,
            "exposedPopulation": 2.0,
            "maximumDepthM": 1.0,
        },
    }
    staging.mkdir(parents=True, exist_ok=True)
    for filename in layers.values():
        (staging / filename).write_bytes(b"png")
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def make_service(cache: Path, *, runner=None, tracker=None) -> Service:
    if runner is None:
        runner = lambda *args: write_manifest(args[8])
    service = Service(
        cache,
        Path("engine"),
        HEADER,
        np.ones((4, 4)),
        np.ones((4, 4)),
        "data",
        "model",
        runner=runner,
        start_worker=False,
        minimum_free_gb=0,
    )
    service.usage = tracker if tracker is not None else FakeTracker()
    return service


@contextlib.contextmanager
def running_server(service: Service):
    server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(service)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        encoded = None if body is None else body.encode("utf-8")
        connection.request(method, path, body=encoded, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response.status, response.getheader("Set-Cookie"), json.loads(payload)
    finally:
        connection.close()


def session_cookie(server, headers=None) -> str:
    with mock.patch("lisflood_runner.service.generate.transform_points", return_value=PROJECTED):
        _, set_cookie, _ = request(
            server, "GET", "/api/lisflood/config", headers=headers or {}
        )
    return set_cookie.split(";", 1)[0]


# ---- the tracker itself ----


def test_session_hash_is_stable_and_does_not_reveal_cookie() -> None:
    raw = new_session_token()

    digest = session_hash(raw, SECRET)

    assert len(digest) == 64
    assert raw not in digest
    assert digest == session_hash(raw, SECRET)
    assert digest != session_hash(raw, "another-secret-with-32-characters")


def test_country_lookup_returns_only_iso_code() -> None:
    class FakeReader:
        def get(self, _ip):
            return {"country": {"iso_code": "au"}}

    assert country_from_ip("203.0.113.5", reader=FakeReader()) == "AU"
    assert country_from_ip("not-an-ip", reader=FakeReader()) == "ZZ"
    assert country_from_ip("203.0.113.5") == "ZZ"
    assert country_from_ip(None, reader=FakeReader()) == "ZZ"


def test_country_lookup_fails_closed_for_missing_or_malformed_dbip_records() -> None:
    class MissingReader:
        def get(self, _ip):
            return None

    class MalformedReader:
        def get(self, _ip):
            return {"country": {"iso_code": "1A"}}

    class BrokenReader:
        def get(self, _ip):
            raise OSError("unreadable database")

    assert country_from_ip("203.0.113.5", reader=MissingReader()) == "ZZ"
    assert country_from_ip("203.0.113.5", reader=MalformedReader()) == "ZZ"
    assert country_from_ip("203.0.113.5", reader=BrokenReader()) == "ZZ"


def test_tracker_posts_only_privacy_safe_fields() -> None:
    tracker = UsageTracker(
        endpoint=ENDPOINT,
        internal_token="token",
        session_secret=SECRET,
        queue_size=4,
    )
    cookie = new_session_token()

    with mock.patch(
        "lisflood_runner.usage.urllib.request.urlopen", return_value=FakeResponse()
    ) as urlopen:
        assert tracker.emit(
            "run_success",
            session_hash=session_hash(cookie, SECRET),
            country_code=tracker.country("203.0.113.5"),
            occurred_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            run_id="0123456789abcdef0123",
        ) is True
        deadline = time.monotonic() + 2.0
        while not urlopen.called and time.monotonic() < deadline:
            time.sleep(0.01)

    outgoing = urlopen.call_args.args[0]
    body = outgoing.data.decode("utf-8")
    payload = json.loads(body)
    headers = {name.lower(): value for name, value in outgoing.header_items()}
    assert set(payload) == EVENT_FIELDS
    assert payload["occurred_at"] == "2026-08-25T00:00:00Z"
    assert payload["run_id"] == "0123456789abcdef0123"
    assert "203.0.113.5" not in body
    assert cookie not in body
    assert headers["x-analytics-token"] == "token"
    assert outgoing.full_url == ENDPOINT


def test_tracker_is_a_no_op_and_returns_false_when_unconfigured() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        tracker = UsageTracker.from_env()

    assert tracker.enabled is False
    assert tracker.emit(
        "session_start", session_hash=session_hash("cookie", SECRET), country_code="AU", run_id=None
    ) is False
    assert tracker.country("203.0.113.5") == "ZZ"


def test_env_configuration_defaults_to_the_lisflood_events_route() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "ANALYTICS_INTERNAL_TOKEN": "token",
            "ANALYTICS_SESSION_SECRET": SECRET,
            "ANALYTICS_GEOIP_DATABASE": "/nonexistent/dbip-country-lite.mmdb",
        },
        clear=True,
    ):
        tracker = UsageTracker.from_env()

    assert tracker.endpoint == ENDPOINT
    assert tracker.enabled is True
    assert tracker.country("203.0.113.5") == "ZZ"


def test_tracker_survives_an_unavailable_collector() -> None:
    sender = RecordingSender(error=OSError("collector down"))
    tracker = UsageTracker(
        endpoint=ENDPOINT,
        internal_token="token",
        session_secret=SECRET,
        sender=sender,
    )

    assert tracker.emit(
        "run_success", session_hash="a" * 64, country_code="AU", run_id="0123456789abcdef0123"
    ) is True
    assert len(sender.wait(1)) == 1

    sender.error = None
    assert tracker.emit(
        "run_failure", session_hash="a" * 64, country_code="AU", run_id="0123456789abcdef0124"
    ) is True
    delivered = sender.wait(2)
    assert [payload["event_type"] for _, _, payload in delivered] == ["run_success", "run_failure"]


def test_tracker_drops_events_instead_of_blocking_when_the_queue_is_full() -> None:
    release = threading.Event()
    tracker = UsageTracker(
        endpoint=ENDPOINT,
        internal_token="token",
        session_secret=SECRET,
        sender=lambda *_args: release.wait(2.0),
        queue_size=1,
    )

    results = [
        tracker.emit(
            "session_start", session_hash="b" * 64, country_code="AU", run_id=None
        )
        for _ in range(200)
    ]
    release.set()

    assert results[0] is True
    assert False in results


# ---- the service ----


def test_config_emits_one_session_start_and_sets_a_private_cookie() -> None:
    tracker = FakeTracker()
    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), tracker=tracker)
        with running_server(service) as server, mock.patch(
            "lisflood_runner.service.generate.transform_points", return_value=PROJECTED
        ):
            status, set_cookie, payload = request(
                server,
                "GET",
                "/api/lisflood/config",
                headers={"X-Real-IP": "203.0.113.5"},
            )
            assert status == 200
            assert payload["schemaVersion"] == 1
            assert set_cookie is not None
            assert "hx_lisflood_session=" in set_cookie
            assert "Max-Age=1800" in set_cookie
            assert "HttpOnly" in set_cookie
            assert "Secure" in set_cookie
            assert "SameSite=Lax" in set_cookie
            cookie = set_cookie.split(";", 1)[0]
            raw_token = cookie.split("=", 1)[1]

            assert [event for event, _ in tracker.events] == ["session_start"]
            _, kwargs = tracker.events[0]
            assert set(kwargs) == {"session_hash", "country_code", "run_id"}
            assert kwargs["country_code"] == "AU"
            assert kwargs["run_id"] is None
            assert len(kwargs["session_hash"]) == 64
            assert raw_token not in json.dumps(tracker.events)
            assert "203.0.113.5" not in json.dumps(tracker.events)
            assert tracker.ips == ["203.0.113.5"]

            # A browser that already carries the cookie is not a new session.
            request(server, "GET", "/api/lisflood/config", headers={"Cookie": cookie})
            assert len(tracker.events) == 1


def test_session_cookie_is_reused_and_the_hash_matches_the_run_event() -> None:
    tracker = FakeTracker()
    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), tracker=tracker)
        with running_server(service) as server:
            cookie = session_cookie(server, headers={"X-Real-IP": "203.0.113.5"})
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds", return_value=SNAPPED
            ):
                status, _, submitted = request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    RUN_BODY,
                    {
                        "Content-Type": "application/json",
                        "Cookie": cookie,
                        "X-Real-IP": "203.0.113.5",
                    },
                )
            assert status == 202
            service.run_next()

            session_start = tracker.events[0][1]
            run_success = tracker.events[-1]
            assert run_success[0] == "run_success"
            assert run_success[1]["session_hash"] == session_start["session_hash"]
            assert run_success[1]["run_id"] == submitted["jobId"]
            assert len(submitted["jobId"]) == 20
            assert "203.0.113.5" not in json.dumps(tracker.events)


def test_failed_job_emits_run_failure_without_error_content() -> None:
    def failing_runner(*_args):
        raise RuntimeError("private failure details")

    tracker = FakeTracker()
    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), runner=failing_runner, tracker=tracker)
        with running_server(service) as server:
            cookie = session_cookie(server)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds", return_value=SNAPPED
            ):
                status, _, submitted = request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    RUN_BODY,
                    {"Content-Type": "application/json", "Cookie": cookie},
                )
            assert status == 202
            assert service.run_next() == {"status": "failed", "error": "Simulation failed"}

    event_type, kwargs = tracker.events[-1]
    assert event_type == "run_failure"
    assert kwargs["run_id"] == submitted["jobId"]
    assert "private failure details" not in json.dumps(tracker.events)


def test_cached_completed_job_is_not_counted_twice() -> None:
    tracker = FakeTracker()
    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), tracker=tracker)
        with running_server(service) as server:
            cookie = session_cookie(server)
            headers = {"Content-Type": "application/json", "Cookie": cookie}
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds", return_value=SNAPPED
            ):
                first_status, _, first = request(
                    server, "POST", "/api/lisflood/run", RUN_BODY, headers
                )
                service.run_next()
                second_status, _, second = request(
                    server, "POST", "/api/lisflood/run", RUN_BODY, headers
                )

    assert (first_status, second_status) == (202, 200)
    assert first["jobId"] == second["jobId"]
    emitted = [event_type for event_type, _ in tracker.events]
    assert emitted == ["session_start", "run_success"]


def test_tracking_failures_never_break_config_or_run() -> None:
    class BrokenTracker:
        def country(self, _client_ip):
            raise RuntimeError("analytics exploded")

        def emit(self, *_args, **_kwargs):
            raise RuntimeError("analytics exploded")

    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), tracker=BrokenTracker())
        with running_server(service) as server:
            with mock.patch(
                "lisflood_runner.service.generate.transform_points", return_value=PROJECTED
            ):
                status, set_cookie, payload = request(
                    server, "GET", "/api/lisflood/config"
                )
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds", return_value=SNAPPED
            ):
                run_status, _, submitted = request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    RUN_BODY,
                    {"Content-Type": "application/json", "X-Real-IP": "203.0.113.5"},
                )
            completed = service.run_next()

    assert status == 200 and payload["schemaVersion"] == 1
    assert set_cookie is not None
    assert run_status == 202
    assert completed["jobId"] == submitted["jobId"]
    assert completed["status"] == "completed"
