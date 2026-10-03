"""Plex library refresh after a download lands.

Mirrors ``app.downloads_hooks.refresh_jellyfin_library``: the credentials are
whatever an administrator saved in Settings, the request is best-effort, and
nothing here raises into the job-finished listener that calls it.
"""

import logging

import requests

from app.auth import models as auth_models

logger = logging.getLogger(__name__)

SETTING_PLEX_URL = "plex_url"
SETTING_PLEX_TOKEN = "plex_token"

_TIMEOUT = 10


def get_config() -> tuple[str, str]:
    return (
        (auth_models.get_setting(SETTING_PLEX_URL) or "").strip(),
        (auth_models.get_setting(SETTING_PLEX_TOKEN) or "").strip(),
    )


def set_config(url: str, token: str) -> None:
    auth_models.set_setting(SETTING_PLEX_URL, (url or "").strip())
    auth_models.set_setting(SETTING_PLEX_TOKEN, (token or "").strip())


def is_connected() -> bool:
    url, token = get_config()
    return bool(url and token)


def _sections(url: str, token: str) -> list[str]:
    response = requests.get(
        f"{url.rstrip('/')}/library/sections",
        headers={"X-Plex-Token": token, "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    directory = (response.json().get("MediaContainer") or {}).get("Directory") or []
    return [str(d["key"]) for d in directory if d.get("key") is not None]


def refresh_libraries() -> tuple[bool, int | None]:
    """Ask Plex to scan every library section. Never raises."""
    url, token = get_config()
    if not url or not token:
        logger.info("Plex refresh skipped: not configured")
        return False, None

    try:
        section_ids = _sections(url, token)
    except Exception as exc:
        logger.warning("Plex refresh failed reading sections: %s", type(exc).__name__)
        return False, None

    if not section_ids:
        return True, None

    ok, last_status = True, None
    for section_id in section_ids:
        try:
            response = requests.get(
                f"{url.rstrip('/')}/library/sections/{section_id}/refresh",
                headers={"X-Plex-Token": token},
                timeout=_TIMEOUT,
            )
            last_status = response.status_code
            if not response.ok:
                ok = False
        except Exception as exc:
            logger.warning("Plex refresh failed for section %s: %s", section_id, type(exc).__name__)
            ok = False
    return ok, last_status


def test_connection() -> tuple[bool, str]:
    """Report whether the saved credentials can reach Plex. Never raises."""
    url, token = get_config()
    if not url or not token:
        return False, "URL o token non configurati"
    try:
        section_ids = _sections(url, token)
    except Exception as exc:
        return False, f"Connessione a Plex non riuscita: {type(exc).__name__}"
    return True, f"{len(section_ids)} librerie trovate"
