"""Radarr: reading its wanted-movie list and importing a download into it."""

import logging

from app.auth import models as auth_models
from app.integrations import arr_client

logger = logging.getLogger(__name__)

SETTING_RADARR_URL = "radarr_url"
SETTING_RADARR_API_KEY = "radarr_api_key"


def get_config() -> tuple[str, str]:
    return (
        (auth_models.get_setting(SETTING_RADARR_URL) or "").strip(),
        (auth_models.get_setting(SETTING_RADARR_API_KEY) or "").strip(),
    )


def set_config(url: str, api_key: str) -> None:
    auth_models.set_setting(SETTING_RADARR_URL, (url or "").strip())
    auth_models.set_setting(SETTING_RADARR_API_KEY, (api_key or "").strip())


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


def get_movie(movie_id) -> dict | None:
    """One movie, shaped exactly like a ``wanted_missing()`` record, so a
    manual resolution after the matcher gave up can reuse the same parsing as
    an automatic one. ``None``, never raising, when Radarr is unreachable or
    the movie is gone."""
    url, api_key = get_config()
    if not url or not api_key:
        return None
    try:
        return arr_client.get(url, api_key, f"movie/{movie_id}")
    except Exception as exc:
        logger.warning("Radarr movie lookup failed: %s", type(exc).__name__)
        return None


def find_missing_movie(tmdb_id: int) -> dict | None:
    """The local Radarr movie for this tmdb_id, if it is monitored and still
    has no file. ``None`` otherwise — including when Radarr is unreachable,
    or when it does have the movie but already has a file for it.

    Checked after *any* finished film, not only ones the sync submitted, so
    a manually downloaded film that happens to be on Radarr's own missing
    list gets the same move-and-import handoff a synced one would — see
    ``app.downloads_hooks._maybe_refresh_radarr``. Matched on tmdb_id, the
    same id ``app.integrations.matching`` confirms a sync match against, so
    this is exact or nothing, never a title that merely sounds right.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return None
    try:
        movies = arr_client.get(url, api_key, "movie", params={"tmdbId": tmdb_id})
    except Exception as exc:
        logger.warning("Radarr movie lookup by tmdb_id failed: %s", type(exc).__name__)
        return None
    for movie in movies or []:
        if movie.get("tmdbId") == tmdb_id:
            if movie.get("monitored") and not movie.get("hasFile"):
                return movie
            logger.info(
                "Radarr: matched tmdb_id %s but it is %s — nothing to import",
                tmdb_id, "already on disk" if movie.get("hasFile") else "not monitored",
            )
            return None
    logger.info("Radarr: no movie for tmdb_id %s", tmdb_id)
    return None


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
    """``movie_id`` omitted rescans every movie; given, it is scoped to just
    that one — use it once ``import_into_library`` has placed a file in its
    folder, so Radarr recognises it without scanning the whole library for
    one new file."""
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Radarr rescan skipped: not configured")
        return False
    fields = {"movieId": movie_id} if movie_id is not None else {}
    return arr_client.post_command(url, api_key, "RescanMovie", **fields)


def import_into_library(output_path: str, movie: dict) -> bool:
    """Move a finished download into ``movie``'s own folder and have Radarr
    pick it up from there.

    Verified against a real Radarr: this is the combination that actually
    works, and the only one that does. Radarr's "scan this folder and
    import" command (``DownloadedMoviesScan``) is for a download its own
    download-client tracking already knows about — pointed at an arbitrary
    folder it has no history for, it silently finds nothing, even mounted
    and reachable. A plain ``RescanMovie``, scoped to one movie whose folder
    now actually holds a file, has no such requirement — and does not care
    what the file is named, only that it is there.
    """
    new_path = arr_client.place_in_library(output_path, movie["path"])
    if new_path is None:
        return False
    return rescan_movie(movie["id"])
