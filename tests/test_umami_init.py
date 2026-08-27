from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from analytics_app.config import Settings
from analytics_app.umami_init import WEBSITE_DOMAIN, UmamiInitError, init_umami


def make_settings(*, umami_username: str = "", umami_password: str = "", umami_website_id: str = "") -> Settings:
    return Settings(
        admin_email="ze.jiang@hhu.edu.cn",
        admin_password_hash="$argon2id$unused",
        internal_token="i" * 32,
        session_secret="s" * 32,
        collected_since=datetime(2026, 8, 1, tzinfo=timezone.utc),
        umami_base_url="http://umami:3000",
        umami_username=umami_username,
        umami_password=umami_password,
        umami_website_id=umami_website_id,
    )


def make_umami_fake():
    """Stateful in-memory stand-in for the Umami v3 admin API."""
    state = {"users": [], "websites": [], "sessions": {}, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        state["calls"].append(f"{method} {path}")
        if path == "/api/auth/login":
            body = json.loads(request.content)
            user = next(
                (u for u in state["users"] if u["username"] == body["username"] and u["password"] == body["password"]),
                None,
            )
            if user is None:
                return httpx.Response(401, json={"error": "invalid credentials"})
            token = f"token-{user['username']}"
            state["sessions"][token] = user["username"]
            return httpx.Response(200, json={"token": token, "user": {"id": user["id"], "username": user["username"]}})
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in state["sessions"]:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/api/admin/users":
            return httpx.Response(200, json=state["users"])
        if path == "/api/users" and method == "POST":
            body = json.loads(request.content)
            user = {"id": f"user-{len(state['users']) + 1}", **body}
            state["users"].append(user)
            return httpx.Response(200, json=user)
        if path.startswith("/api/users/") and method == "POST":
            user_id = path.rsplit("/", 1)[-1]
            body = json.loads(request.content)
            user = next(u for u in state["users"] if u["id"] == user_id)
            user.update(body)
            return httpx.Response(200, json=user)
        if path == "/api/websites":
            if method == "GET":
                return httpx.Response(200, json=state["websites"])
            if method == "POST":
                body = json.loads(request.content)
                site = {"id": f"site-{len(state['websites']) + 1}", **body}
                state["websites"].append(site)
                return httpx.Response(200, json=site)
        raise AssertionError(f"unexpected request: {method} {path}")

    return state, handler


def seed_defaults(state) -> None:
    state["users"].append({"id": "user-1", "username": "admin", "password": "umami", "role": "admin"})


def test_fresh_bootstrap_creates_reader_and_website() -> None:
    state, handler = make_umami_fake()
    seed_defaults(state)
    client = httpx.Client(base_url="http://umami:3000", transport=httpx.MockTransport(handler))

    result = init_umami(make_settings(), http_client=client)

    assert result["UMAMI_API_USERNAME"] == "dashboard-reader"
    assert len(result["UMAMI_API_PASSWORD"]) >= 32
    assert result["UMAMI_WEBSITE_ID"] == "site-1"
    reader = next(u for u in state["users"] if u["username"] == "dashboard-reader")
    assert reader["role"] == "user"
    assert reader["password"] == result["UMAMI_API_PASSWORD"]
    assert state["websites"][0]["domain"] == WEBSITE_DOMAIN
    assert "POST /api/users" in state["calls"]
    assert "POST /api/websites" in state["calls"]


def test_rerun_is_idempotent_and_creates_nothing() -> None:
    state, handler = make_umami_fake()
    seed_defaults(state)
    state["users"].append({"id": "user-2", "username": "dashboard-reader", "password": "existing-reader-pw", "role": "user"})
    state["websites"].append({"id": "site-9", "name": "HydroclimateX", "domain": WEBSITE_DOMAIN})
    client = httpx.Client(base_url="http://umami:3000", transport=httpx.MockTransport(handler))

    result = init_umami(
        make_settings(umami_username="dashboard-reader", umami_password="existing-reader-pw"),
        http_client=client,
    )

    assert result == {
        "UMAMI_API_USERNAME": "dashboard-reader",
        "UMAMI_API_PASSWORD": "existing-reader-pw",
        "UMAMI_WEBSITE_ID": "site-9",
    }
    assert "POST /api/users" not in state["calls"]
    assert "POST /api/websites" not in state["calls"]


def test_reader_exists_but_password_unknown_rotates_reader_password() -> None:
    state, handler = make_umami_fake()
    seed_defaults(state)
    state["users"].append({"id": "user-2", "username": "dashboard-reader", "password": "unknown-old-pw", "role": "user"})
    state["websites"].append({"id": "site-9", "name": "HydroclimateX", "domain": WEBSITE_DOMAIN})
    client = httpx.Client(base_url="http://umami:3000", transport=httpx.MockTransport(handler))

    result = init_umami(make_settings(umami_username="dashboard-reader"), http_client=client)

    reader = next(u for u in state["users"] if u["username"] == "dashboard-reader")
    assert reader["password"] == result["UMAMI_API_PASSWORD"]
    assert "POST /api/users/user-2" in state["calls"]
    assert "POST /api/websites" not in state["calls"]


def test_admin_login_failure_raises_clear_error() -> None:
    state, handler = make_umami_fake()
    client = httpx.Client(base_url="http://umami:3000", transport=httpx.MockTransport(handler))

    with pytest.raises(UmamiInitError, match="Umami login failed"):
        init_umami(make_settings(), http_client=client)


def test_admin_credentials_default_to_admin_umami_and_are_overridable(monkeypatch) -> None:
    required = {
        "ANALYTICS_ADMIN_PASSWORD_HASH": "$argon2id$unused",
        "ANALYTICS_INTERNAL_TOKEN": "i" * 32,
        "ANALYTICS_SESSION_SECRET": "s" * 32,
        "ANALYTICS_DATABASE_URL": "postgresql://analytics:test@database/analytics",
        "ANALYTICS_SMTP_AUTHORIZATION_CODE": "smtp-code",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    for name in ("UMAMI_ADMIN_USERNAME", "UMAMI_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env().umami_admin_username == "admin"
    assert Settings.from_env().umami_admin_password == "umami"

    monkeypatch.setenv("UMAMI_ADMIN_USERNAME", "ops")
    monkeypatch.setenv("UMAMI_ADMIN_PASSWORD", "ops-secret")
    assert Settings.from_env().umami_admin_username == "ops"
    assert Settings.from_env().umami_admin_password == "ops-secret"
