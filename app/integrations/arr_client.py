"""Minimal HTTP client shared by the Sonarr and Radarr connectors.

Both are *Arr applications and expose the same v3 API shape — ``X-Api-Key``
header, ``/api/v3/...`` paths, a ``POST /command`` endpoint for actions — so
one client serves both; ``app.integrations.sonarr``/``radarr`` only know their
own resource and command names.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15


def get(base_url: str, api_key: str, path: str, params: dict | None = None):
    """Raises on failure — callers decide whether that means "unreachable"
    (worth reporting) or "skip this cycle" (worth swallowing)."""
    response = requests.get(
        f"{base_url.rstrip('/')}/api/v3/{path.lstrip('/')}",
        headers={"X-Api-Key": api_key},
        params=params or {},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def post_command(base_url: str, api_key: str, name: str, **fields) -> bool:
    """Fire a command and report whether it was accepted. Never raises."""
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/v3/command",
            headers={"X-Api-Key": api_key},
            json={"name": name, **fields},
            timeout=_TIMEOUT,
        )
        if not response.ok:
            logger.warning("%s command returned HTTP %d", name, response.status_code)
        return response.ok
    except Exception as exc:
        logger.warning("%s command failed: %s", name, type(exc).__name__)
        return False


def system_status(base_url: str, api_key: str) -> dict:
    """Raises on failure; used specifically to test a connection."""
    return get(base_url, api_key, "system/status")


def wanted_missing_page(
    base_url: str, api_key: str, page: int, page_size: int = 50,
    extra_params: dict | None = None,
) -> dict:
    params = {
        "page": page, "pageSize": page_size,
        "sortKey": "airDateUtc", "sortDirection": "ascending",
    }
    if extra_params:
        params.update(extra_params)
    return get(base_url, api_key, "wanted/missing", params=params)


def wanted_missing_all(
    base_url: str, api_key: str, max_pages: int = 20, page_size: int = 50,
    extra_params: dict | None = None,
) -> list[dict]:
    """Every page of wanted/missing, capped so a huge library cannot loop
    forever. Raises on the first failed page — the caller decides what an
    unreachable Sonarr/Radarr means for this cycle."""
    records = []
    for page_number in range(1, max_pages + 1):
        payload = wanted_missing_page(base_url, api_key, page_number, page_size, extra_params)
        batch = payload.get("records") or []
        records.extend(batch)
        if len(batch) < page_size:
            break
    return records
