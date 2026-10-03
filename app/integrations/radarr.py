"""Radarr: reading its wanted-movie list and rescanning after a download."""

import logging

from app.auth import models as auth_models
from app.integrations import arr_client

logger = logging.getLogger(__name__)

SETTING_RADARR_URL = "radarr_url"
SETTING_RADARR_API_KEY = "radarr_api_key"
SETTING_RADARR_IMPORT_DIR = "radarr_import_dir"


def get_config() -> tuple[str, str]:
    return (
        (auth_models.get_setting(SETTING_RADARR_URL) or "").strip(),
        (auth_models.get_setting(SETTING_RADARR_API_KEY) or "").strip(),
    )


def set_config(url: str, api_key: str) -> None:
    auth_models.set_setting(SETTING_RADARR_URL, (url or "").strip())
    auth_models.set_setting(SETTING_RADARR_API_KEY, (api_key or "").strip())


def get_import_dir() -> str:
    """A staging folder Radarr itself can see, or "" when imports are off.

    When set, a synced movie is downloaded here instead of the panel's own
    library, and ``import_scan`` asks Radarr to move it from here into its
    own — Radarr matches the file by parsing its name, so this is unset by
    default rather than guessed at.
    """
    return (auth_models.get_setting(SETTING_RADARR_IMPORT_DIR) or "").strip()


def set_import_dir(path: str) -> None:
    auth_models.set_setting(SETTING_RADARR_IMPORT_DIR, (path or "").strip())


def is_connected() -> bool:
    url, api_key = get_config()
    return bool(url and api_key)


def test_connection() -> tuple[bool, str]:
    url, api_key = get_config()
    if not url or not api_key:
        return False, "URL o API key non configurati"
    try:
        status = arr_client.system_status(url, api_key)
    except Exception as exc:
        return False, f"Connessione a Radarr non riuscita: {type(exc).__name__}"
    return True, f"Radarr {status.get('version', '')} raggiunto".strip()


def wanted_missing() -> list[dict]:
    """Monitored movies Radarr has not downloaded yet.

    Each record is a Radarr movie resource, carrying its own ``tmdbId`` —
    unlike Sonarr's episodes, no extra include is needed for it.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return []
    try:
        return arr_client.wanted_missing_all(url, api_key)
    except Exception as exc:
        logger.warning("Radarr wanted/missing failed: %s", type(exc).__name__)
        return []


def rescan_movie(movie_id: int | None = None) -> bool:
    """``movie_id`` omitted rescans every movie.

    A rescan only confirms a file that is already in the right place; it
    moves nothing. See ``import_scan`` for the staging-folder handoff.
    """
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Radarr rescan skipped: not configured")
        return False
    fields = {"movieId": movie_id} if movie_id is not None else {}
    return arr_client.post_command(url, api_key, "RescanMovie", **fields)


def import_scan(path: str) -> bool:
    """Ask Radarr to import whatever finished movie sits in ``path``.

    Unlike ``rescan_movie``, this moves the file: Radarr scans ``path``,
    matches it against its monitored movies, and relocates it into its own
    library under its own naming. ``path`` has to be something Radarr's own
    filesystem can see — normally the same staging folder this panel
    downloaded into, on a volume shared with it.
    """
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Radarr import scan skipped: not configured")
        return False
    return arr_client.post_command(
        url, api_key, "DownloadedMoviesScan", path=path, importMode="Move"
    )
