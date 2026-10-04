"""Minimal HTTP client shared by the Sonarr and Radarr connectors.

Both are *Arr applications and expose the same v3 API shape — ``X-Api-Key``
header, ``/api/v3/...`` paths, a ``POST /command`` endpoint for actions — so
one client serves both; ``app.integrations.sonarr``/``radarr`` only know their
own resource and command names.
"""

import glob
import logging
import os
import shutil
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15

# How long to wait for a fire-and-forget command (a rescan) to actually
# finish before giving up — see post_command's docstring for why this
# matters at all.
_COMMAND_POLL_TIMEOUT = 30
_COMMAND_POLL_INTERVAL = 1.0


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


def post_command(base_url: str, api_key: str, name: str, wait: bool = False, **fields) -> bool:
    """Fire a command and report whether it was accepted — or, with
    ``wait=True``, block until Sonarr/Radarr reports it actually finished.

    Sonarr/Radarr queue a command and answer the POST immediately, long
    before the command itself has run — verified against a real Sonarr,
    where firing ``RenameSeries`` right after ``RescanSeries`` reorganised
    nothing, because the rescan had not finished long enough to have
    noticed the file yet. Anything that fires a second command depending on
    the first (a rename after the rescan that is supposed to make
    Sonarr/Radarr aware of the file at all) needs ``wait=True``; a rescan
    with nothing queued after it does not.
    """
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/v3/command",
            headers={"X-Api-Key": api_key},
            json={"name": name, **fields},
            timeout=_TIMEOUT,
        )
        if not response.ok:
            logger.warning("%s command returned HTTP %d", name, response.status_code)
            return False
        command_id = response.json().get("id")
    except Exception as exc:
        logger.warning("%s command failed: %s", name, type(exc).__name__)
        return False

    if not wait or command_id is None:
        return True

    deadline = time.monotonic() + _COMMAND_POLL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            status = get(base_url, api_key, f"command/{command_id}")
        except Exception as exc:
            logger.warning("%s command status check failed: %s", name, type(exc).__name__)
            return True
        if status.get("status") in ("completed", "failed"):
            return status.get("status") == "completed"
        time.sleep(_COMMAND_POLL_INTERVAL)

    logger.warning("%s command did not finish within %ss", name, _COMMAND_POLL_TIMEOUT)
    return False


def system_status(base_url: str, api_key: str) -> dict:
    """Raises on failure; used specifically to test a connection."""
    return get(base_url, api_key, "system/status")


def resolve_tag_id(base_url: str, api_key: str, label: str) -> int | None:
    """The id behind a tag's label — Sonarr/Radarr filter movies/series by
    tag id, never by the name an admin typed. Case-insensitive, since the tag
    picker in each app's own UI already lower-cases labels on save. ``None``
    if no such tag exists (including when Sonarr/Radarr is unreachable)."""
    label = (label or "").strip().lower()
    if not label:
        return None
    try:
        tags = get(base_url, api_key, "tag")
    except Exception as exc:
        logger.warning("Tag lookup failed: %s", type(exc).__name__)
        return None
    for tag in tags or []:
        if (tag.get("label") or "").strip().lower() == label:
            return tag.get("id")
    return None


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


def place_in_library(output_path: str, target_dir: str) -> str | None:
    """Move a finished download (and any sidecar sharing its stem — mainly a
    subtitle) into ``target_dir``, which Sonarr/Radarr already consider that
    movie's or series' own folder.

    Verified against real Sonarr and Radarr: ``RescanMovie``/``RescanSeries``
    recognise a file sitting in that exact folder regardless of its name —
    scoped to one title, there is no ambiguity to resolve by parsing a
    filename the way there would be scanning an arbitrary folder. That is
    also why this moves the file there directly instead of asking
    Sonarr/Radarr to import it from wherever it landed:
    ``DownloadedMoviesScan``/``DownloadedEpisodesScan`` target a download
    client's own completed-downloads folder, tied to a grab Sonarr/Radarr
    themselves made, and found nothing for a file with no such history even
    with the path mounted and reachable.

    Returns the file's new path, or ``None`` if the move failed — logged,
    never raised, since the caller falls back to a blind rescan either way.
    """
    stem = os.path.splitext(output_path)[0]
    source_dir = os.path.dirname(output_path)
    try:
        os.makedirs(target_dir, exist_ok=True)
        new_path = None
        for sibling in glob.glob(f"{stem}.*"):
            destination = os.path.join(target_dir, os.path.basename(sibling))
            shutil.move(sibling, destination)
            if os.path.abspath(sibling) == os.path.abspath(output_path):
                new_path = destination
    except Exception:
        logger.exception("Could not move %s into %s", output_path, target_dir)
        return None

    # The panel's own per-title folder (e.g. "Title (Year)/") is left behind
    # empty rather than deleted outright — one level only, and only if moving
    # the file really did empty it, so a poster or .nfo some other tool left
    # there is never silently discarded.
    try:
        if source_dir and not os.listdir(source_dir):
            os.rmdir(source_dir)
    except OSError:
        pass

    return new_path
