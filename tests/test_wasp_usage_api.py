import importlib.util
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


class FakeTracker:
    def __init__(self):
        self.events = []

    def country(self, _client_ip):
        return "AU"

    def emit(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))
        return True


def load_backend(result):
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    prediction = types.ModuleType("wasp.prediction")
    prediction.run_wasp_prediction = lambda **_kwargs: result.copy()
    utilities = types.ModuleType("wasp.utils")
    utilities.get_demo_csv = lambda _name: b"y,x\n1,2\n"
    sys.modules["wasp.prediction"] = prediction
    sys.modules["wasp.utils"] = utilities
    spec = importlib.util.spec_from_file_location("wasp_api_analytics_test", ROOT / "backend" / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    tracker = FakeTracker()
    module.USAGE_TRACKER = tracker
    return module, tracker


def test_usage_session_sets_private_30_minute_cookie() -> None:
    module, tracker = load_backend({"success": True})
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")

    response = client.post("/api/usage/session", headers={"X-Real-IP": "203.0.113.5"})

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "hx_wasp_session=" in cookie
    assert "Max-Age=1800" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert tracker.events[0][0] == "session_start"
    assert tracker.events[0][1]["country_code"] == "AU"


def test_successful_prediction_adds_run_id_and_emits_success() -> None:
    module, tracker = load_backend({
        "success": True,
        "metrics": {"wasp": {"nse": 0.9}},
    })
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")
    client.post("/api/usage/session")

    response = client.post(
        "/api/wasp/predict",
        files={"file": ("private-name.csv", b"y,x\n1,2\n", "text/csv")},
    )

    assert response.status_code == 200
    run_id = response.json()["analytics_run_id"]
    assert len(run_id) == 36
    outcome = tracker.events[-1]
    assert outcome[0] == "run_success"
    assert outcome[1]["run_id"] == run_id
    assert "private-name.csv" not in str(tracker.events)


def test_failed_prediction_emits_failure_without_error_content() -> None:
    module, tracker = load_backend({"success": False, "message": "private failure details"})
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")
    client.post("/api/usage/session")

    response = client.post(
        "/api/wasp/predict",
        files={"file": ("private-name.csv", b"y,x\n1,2\n", "text/csv")},
    )

    assert response.status_code == 400
    assert tracker.events[-1][0] == "run_failure"
    assert "private failure details" not in str(tracker.events)
    assert "private-name.csv" not in str(tracker.events)


def test_download_endpoint_accepts_run_id_and_emits_download() -> None:
    module, tracker = load_backend({"success": True})
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")
    client.post("/api/usage/session")

    response = client.post(
        "/api/usage/download",
        json={"run_id": "267eb25f-e2e5-4654-bf41-2bdcfbdedddc"},
    )

    assert response.status_code == 202
    assert tracker.events[-1][0] == "download"
    assert tracker.events[-1][1]["run_id"] == "267eb25f-e2e5-4654-bf41-2bdcfbdedddc"


def test_software_download_endpoint_emits_download_for_each_language() -> None:
    module, tracker = load_backend({"success": True})
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")
    client.post("/api/usage/session")

    for software in ("r", "python", "matlab"):
        response = client.post("/api/usage/software-download", json={"software": software})

        assert response.status_code == 202
        assert tracker.events[-1][0] == "download"
        assert tracker.events[-1][1]["run_id"] == str(module.SOFTWARE_DOWNLOAD_IDS[software])


def test_software_download_rejects_unknown_language() -> None:
    module, tracker = load_backend({"success": True})
    client = TestClient(module.app, base_url="https://wasp.hydroclimatex.test")

    response = client.post("/api/usage/software-download", json={"software": "fortran"})

    assert response.status_code == 422
    assert tracker.events == []
