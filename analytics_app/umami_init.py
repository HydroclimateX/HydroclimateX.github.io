from __future__ import annotations

import secrets

import httpx

from .config import Settings


WEBSITE_NAME = "HydroclimateX"
WEBSITE_DOMAIN = "hydroclimatex.com,www.hydroclimatex.com"
READER_ROLE = "user"


class UmamiInitError(RuntimeError):
    pass


def _rows(response: httpx.Response) -> list[dict[str, object]]:
    data = response.json()
    return data if isinstance(data, list) else data.get("data", [])


def _login(http: httpx.Client, username: str, password: str) -> str:
    response = http.post("/api/auth/login", json={"username": username, "password": password})
    if response.status_code == 401:
        raise UmamiInitError(f"Umami login failed for '{username}': invalid credentials")
    response.raise_for_status()
    return response.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _find_reader(http: httpx.Client, admin_token: str, username: str) -> dict[str, object] | None:
    response = http.get("/api/admin/users", params={"search": username}, headers=_bearer(admin_token))
    response.raise_for_status()
    for user in _rows(response):
        if user.get("username") == username:
            return user
    return None


def _find_website(http: httpx.Client, token: str, domain: str) -> dict[str, object] | None:
    response = http.get("/api/websites", headers=_bearer(token))
    response.raise_for_status()
    for site in _rows(response):
        if site.get("domain") == domain:
            return site
    return None


def init_umami(settings: Settings, *, http_client: httpx.Client | None = None) -> dict[str, str]:
    """Idempotently ensure the Umami reader user and website exist.

    Returns the three credentials deploy-analytics.sh writes into .env:
    UMAMI_API_USERNAME, UMAMI_API_PASSWORD, UMAMI_WEBSITE_ID.

    Runs inside the analytics-api container, which reaches Umami on the
    private docker network (http://umami:3000). Safe to run repeatedly: an
    existing reader/website is matched and reused, never duplicated. If the
    reader exists but its password is unknown (an interrupted earlier run),
    the admin rotates it so the loop self-heals.
    """
    http = http_client or httpx.Client(base_url=settings.umami_base_url, timeout=8.0)
    admin_token = _login(http, settings.umami_admin_username, settings.umami_admin_password)

    reader_username = settings.umami_username or "dashboard-reader"
    reader = _find_reader(http, admin_token, reader_username)
    if reader is None:
        reader_password = secrets.token_urlsafe(24)
        response = http.post(
            "/api/users",
            json={"username": reader_username, "password": reader_password, "role": READER_ROLE},
            headers=_bearer(admin_token),
        )
        response.raise_for_status()
    elif settings.umami_password:
        reader_password = settings.umami_password
    else:
        reader_password = secrets.token_urlsafe(24)
        response = http.post(
            f"/api/users/{reader['id']}",
            json={"password": reader_password},
            headers=_bearer(admin_token),
        )
        response.raise_for_status()

    reader_token = _login(http, reader_username, reader_password)
    website = _find_website(http, reader_token, WEBSITE_DOMAIN)
    if website is None:
        response = http.post(
            "/api/websites",
            json={"name": WEBSITE_NAME, "domain": WEBSITE_DOMAIN},
            headers=_bearer(reader_token),
        )
        response.raise_for_status()
        website = response.json()

    return {
        "UMAMI_API_USERNAME": reader_username,
        "UMAMI_API_PASSWORD": reader_password,
        "UMAMI_WEBSITE_ID": str(website["id"]),
    }
