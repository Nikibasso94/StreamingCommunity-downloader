"""Sonarr: reading its wanted-episode list and importing a download into it."""

import logging

from app.auth import models as auth_models
from app.integrations import arr_client

logger = logging.getLogger(__name__)

SETTING_SONARR_URL = "sonarr_url"
SETTING_SONARR_API_KEY = "sonarr_api_key"
SETTING_SONARR_SKIP_TAG = "sonarr_skip_tag"


def get_config() -> tuple[str, str]:
    return (
        (auth_models.get_setting(SETTING_SONARR_URL) or "").strip(),
        (auth_models.get_setting(SETTING_SONARR_API_KEY) or "").strip(),
    )


def set_config(url: str, api_key: str) -> None:
    auth_models.set_setting(SETTING_SONARR_URL, (url or "").strip())
    auth_models.set_setting(SETTING_SONARR_API_KEY, (api_key or "").strip())


def get_skip_tag() -> str:
    """A Sonarr tag — on the series, not the episode — that keeps a show out
    of this panel entirely: synced, and exempt from the generic post-download
    match too, since the point of the tag is "hands off this one"."""
    return (auth_models.get_setting(SETTING_SONARR_SKIP_TAG) or "").strip()


def set_skip_tag(label: str) -> None:
    auth_models.set_setting(SETTING_SONARR_SKIP_TAG, (label or "").strip())


def _is_skipped(series: dict, url: str, api_key: str) -> bool:
    label = get_skip_tag()
    if not label:
        return False
    tag_id = arr_client.resolve_tag_id(url, api_key, label)
    return tag_id is not None and tag_id in (series.get("tags") or [])


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
    missing list gets the same move-and-import handoff a synced one would —
    see ``app.downloads_hooks._maybe_refresh_sonarr``. The source gives no
    id to confirm a series by, so the series itself is matched by title the
    same conservative way ``app.integrations.matching`` does for the sync.

    The series is embedded under ``series``, the same shape ``get_episode``
    already returns, since placing the file needs the series' own folder
    (``series["path"]``), not just which episode it is.
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
        logger.info(
            "Sonarr: no series close enough to «%s» (best: %r, score %.2f)",
            series_title, best.get("title") if best else None, best_score,
        )
        return None

    if _is_skipped(best, url, api_key):
        logger.info("Sonarr: «%s» is tagged to skip — not importing", best["title"])
        return None

    try:
        episodes = arr_client.get(url, api_key, "episode", params={"seriesId": best["id"]})
    except Exception as exc:
        logger.warning("Sonarr episode lookup failed: %s", type(exc).__name__)
        return None
    for episode in episodes or []:
        if episode.get("seasonNumber") == season and str(episode.get("episodeNumber")) == str(episode_number):
            if episode.get("monitored") and not episode.get("hasFile"):
                return {**episode, "series": best}
            logger.info(
                "Sonarr: matched «%s» S%02dE%s but it is %s — nothing to import",
                best["title"], season, episode_number,
                "already on disk" if episode.get("hasFile") else "not monitored",
            )
            return None
    logger.info(
        "Sonarr: matched series «%s» but it has no S%02dE%s", best["title"], season, episode_number,
    )
    return None


def wanted_missing() -> list[dict]:
    """Monitored episodes Sonarr has not downloaded yet, series included,
    minus any whose series carries the configured skip tag.

    Empty — never raising — when unconfigured or unreachable: a sync cycle
    that cannot reach Sonarr this time has nothing to do, not a reason to
    crash the loop it shares with everything else scheduled alongside it.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return []
    try:
        episodes = arr_client.wanted_missing_all(url, api_key, extra_params={"includeSeries": "true"})
    except Exception as exc:
        logger.warning("Sonarr wanted/missing failed: %s", type(exc).__name__)
        return []

    label = get_skip_tag()
    if not label:
        return episodes
    tag_id = arr_client.resolve_tag_id(url, api_key, label)
    if tag_id is None:
        return episodes
    return [e for e in episodes if tag_id not in ((e.get("series") or {}).get("tags") or [])]


def rescan_series(series_id: int | None = None) -> bool:
    """``series_id`` omitted rescans every series; given, it is scoped to
    just that one — use it once ``import_into_library`` has placed a file in
    its folder, so Sonarr recognises it without scanning the whole library
    for one new episode."""
    url, api_key = get_config()
    if not url or not api_key:
        logger.info("Sonarr rescan skipped: not configured")
        return False
    fields = {"seriesId": series_id} if series_id is not None else {}
    return arr_client.post_command(url, api_key, "RescanSeries", **fields)


def rename_series(series_id: int) -> bool:
    """Ask Sonarr to reorganise this series into its own folder/filename
    convention (``seasonFolderFormat``, and ``standardEpisodeFormat`` when
    "Rename episodes" is on in Media Management).

    Verified against a real Sonarr: dropping a file straight into the
    series' root folder (what ``import_into_library`` does) leaves it there
    — Sonarr recognises it on a rescan but does not reorganise it on its
    own. This command is what actually creates the season subfolder and
    moves the file into it, renaming it too if the toggle is on; skipped
    quietly if "Rename episodes" is off, since the filename is then exactly
    what the admin asked Sonarr to leave alone.
    """
    url, api_key = get_config()
    if not url or not api_key:
        return False
    return arr_client.post_command(url, api_key, "RenameSeries", seriesIds=[series_id])


def import_into_library(output_path: str, episode: dict) -> bool:
    """Move a finished download into the episode's series folder and have
    Sonarr pick it up from there, then let it reorganise that series into
    its own season-folder/filename convention.

    Verified against a real Sonarr: this is the combination that actually
    works, and the only one that does. Sonarr's "scan this folder and
    import" command (``DownloadedEpisodesScan``) is for a download its own
    download-client tracking already knows about — pointed at an arbitrary
    folder it has no history for, it silently finds nothing, even mounted
    and reachable. A plain ``RescanSeries``, scoped to the series whose
    folder now actually holds a file, has no such requirement. It does
    still need a season/episode number somewhere in the filename to tell
    episodes apart within the series — unlike a film, where the whole
    folder is unambiguously one title — but not Sonarr's own naming scheme;
    an arbitrary name carrying "S01E04" was picked up exactly like one
    carrying its preferred format. ``rename_series`` runs after, not
    instead: the rescan is what makes Sonarr aware of the file at all, and
    only an already-known file can be renamed.
    """
    series = episode.get("series") or {}
    new_path = arr_client.place_in_library(output_path, series["path"])
    if new_path is None:
        return False
    if not rescan_series(series["id"]):
        return False
    rename_series(series["id"])
    return True
