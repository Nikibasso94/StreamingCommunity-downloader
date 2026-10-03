"""Sonarr: reading its wanted-episode list and rescanning after a download."""

import logging

from app.auth import models as auth_models
from app.integrations import arr_client

logger = logging.getLogger(__name__)

SETTING_SONARR_URL = "sonarr_url"
SETTING_SONARR_API_KEY = "sonarr_api_key"
SETTING_SONARR_IMPORT_DIR = "sonarr_import_dir"


def get_config() -> tuple[str, str]:
    return (
        (auth_models.get_setting(SETTING_SONARR_URL) or "").strip(),
        (auth_models.get_setting(SETTING_SONARR_API_KEY) or "").strip(),
    )


def set_config(url: str, api_key: str) -> None:
    auth_models.set_setting(SETTING_SONARR_URL, (url or "").strip())
    auth_models.set_setting(SETTING_SONARR_API_KEY, (api_key or "").strip())


def get_import_dir() -> str:
    """A staging folder Sonarr itself can see, or "" when imports are off.

    When set, a synced episode is downloaded here instead of the panel's own
    library, and ``import_scan`` asks Sonarr to move it from here into its
    own — Sonarr parses the filename to tell which episode it is, so this is
    unset by default rather than guessed at.
    """
    return (auth_models.get_setting(SETTING_SONARR_IMPORT_DIR) or "").strip()


def set_import_dir(path: str) -> None:
    auth_models.set_setting(SETTING_SONARR_IMPORT_DIR, (path or "").strip())


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
        return False, f"Connessione a Sonarr non riuscita: {type(exc).__name__}"
    return True, f"Sonarr {status.get('version', '')} raggiunto".strip()


def get_episode(episode_id) -> dict | None:
    """One episode, shaped exactly like a ``wanted_missing()`` record — series
    info nested under ``series`` — so a manual resolution after the matcher
    gave up can reuse the same parsing as an automatic one. ``None``, never
    raising, when Sonarr is unreachable or the episode is gone."""
    url, api_key = get_config()
    if not url or not api_key:
        return None
    try:
        episode = arr_client.get(url, api_key, f"episode/{episode_id}")
        series = arr_client.get(url, api_key, f"series/{episode['seriesId']}")
        return {**episode, "series": series}
    except Exception as exc:
        logger.warning("Sonarr episode lookup failed: %s", type(exc).__name__)
        return None


def find_missing_episode(series_title: str, season: int, episode_number) -> dict | None:
    """The local Sonarr episode for this series/season/episode, if it is
    monitored and still has no file. ``None`` otherwise — including when
    Sonarr is unreachable, when no series matches closely enough, or when it
    does have the episode but already has a file for it.

    Checked after *any* finished episode, not only ones the sync submitted,
    so a manually downloaded episode that happens to be on Sonarr's own
    missing list gets the same move-and-rename handoff a synced one would —
    see ``app.downloads_hooks._maybe_refresh_sonarr``. The source gives no
    id to confirm a series by, so the series itself is matched by title the
    same conservative way ``app.integrations.matching`` does for the sync.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return None
    try:
        all_series = arr_client.get(url, api_key, "series")
    except Exception as exc:
        logger.warning("Sonarr series lookup failed: %s", type(exc).__name__)
        return None

    from app.integrations.matching import SERIES_MATCH_THRESHOLD, _title_score

    best, best_score = None, 0.0
    for series in all_series or []:
        score = _title_score(series_title, series.get("title") or "")
        if score > best_score:
            best, best_score = series, score
    if best is None or best_score < SERIES_MATCH_THRESHOLD:
        return None

    try:
        episodes = arr_client.get(url, api_key, "episode", params={"seriesId": best["id"]})
    except Exception as exc:
        logger.warning("Sonarr episode lookup failed: %s", type(exc).__name__)
        return None
    for episode in episodes or []:
        if episode.get("seasonNumber") == season and str(episode.get("episodeNumber")) == str(episode_number) \
                and episode.get("monitored") and not episode.get("hasFile"):
            return episode
    return None


def wanted_missing() -> list[dict]:
    """Monitored episodes Sonarr has not downloaded yet, series included.

    Empty — never raising — when unconfigured or unreachable: a sync cycle
    that cannot reach Sonarr this time has nothing to do, not a reason to
    crash the loop it shares with everything else scheduled alongside it.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return []
    try:
        return arr_client.wanted_missing_all(url, api_key, extra_params={"includeSeries": "true"})
    except Exception as exc:
        logger.warning("Sonarr wanted/missing failed: %s", type(exc).__name__)
        return []


def rescan_series(series_id: int | None = None) -> bool:
    """``series_id`` omitted rescans every series — the same "one switch,
    whole library" shape as the Jellyfin/Plex refresh.

    A rescan only confirms a file that is already in the right place; it
    moves nothing. See ``import_scan`` for the staging-folder handoff.
    """
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Sonarr rescan skipped: not configured")
        return False
    fields = {"seriesId": series_id} if series_id is not None else {}
    return arr_client.post_command(url, api_key, "RescanSeries", **fields)


def import_scan(path: str) -> bool:
    """Ask Sonarr to import whatever finished episode sits in ``path``.

    Unlike ``rescan_series``, this moves the file: Sonarr scans ``path``,
    parses the filename to work out which episode it is, and relocates it
    into its own library under its own naming. ``path`` has to be something
    Sonarr's own filesystem can see — normally the same staging folder this
    panel downloaded into, on a volume shared with it.
    """
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Sonarr import scan skipped: not configured")
        return False
    return arr_client.post_command(
        url, api_key, "DownloadedEpisodesScan", path=path, importMode="Move"
    )
