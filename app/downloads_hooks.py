"""Telling something else that a download finished.

Two things, deliberately kept apart:

**The Jellyfin refresh.** A one-switch version of the only reason most people
would want a hook at all: the file is in the library, and Jellyfin does not know
until its next scan. It uses the URL and API key already stored for
authentication, so there is nothing to configure.

**Webhooks.** A URL, a method, optional headers and a body template, fired when
a download reaches a terminal state.

There is no shell hook and there will not be one. Open mode grants
MANAGE_SETTINGS to every anonymous visitor (``app/auth/deps.py``), so a command
column here would be remote code execution for anyone who can reach the panel.

A webhook is still an outbound request to an address a settings manager chose,
and pointing it at ``192.168.x`` is the *point* — that is where Jellyfin lives.
The mitigation is therefore not to restrict the address but to make the request
blind: the response body is never returned to the caller and never logged, only
its status code. Whoever configures a hook learns whether it worked, not what
the other end said.
"""

import json
import logging
import threading

import requests

from app import db
from app.requests import models as request_models

logger = logging.getLogger(__name__)

# Which terminal states a hook can subscribe to. Same vocabulary as the job
# status, minus the states that are not terminal.
HOOK_EVENTS = ("done", "error", "cancelled")

ALLOWED_METHODS = ("POST", "PUT", "GET")

_TIMEOUT = 10

_listener_registered = False
_listener_lock = threading.Lock()


# ── CRUD ──────────────────────────────────────────────────────────────────────

def _row_to_hook(row) -> dict:
    hook = dict(row)
    hook["enabled"] = bool(hook["enabled"])
    hook["events"] = json.loads(hook["events"])
    hook["headers"] = json.loads(hook["headers"])
    return hook


def list_hooks() -> list[dict]:
    return [_row_to_hook(r) for r in db.query("SELECT * FROM jf_download_hook ORDER BY id")]


def get_hook(hook_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM jf_download_hook WHERE id = ?", (hook_id,))
    return _row_to_hook(row) if row else None


def create_hook(*, name: str, url: str, method: str = "POST", headers: dict | None = None,
                body_template: str = "", events: list[str] | None = None,
                enabled: bool = True) -> dict:
    timestamp = request_models.now_iso()
    cursor = db.execute(
        "INSERT INTO jf_download_hook"
        "(name, url, method, headers, body_template, events, enabled, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, url, method, json.dumps(headers or {}), body_template,
         json.dumps(events or []), int(enabled), timestamp, timestamp),
    )
    return get_hook(cursor.lastrowid)


def update_hook(hook_id: int, **fields) -> dict | None:
    columns, values = [], []
    for key in ("name", "url", "method", "headers", "body_template", "events", "enabled"):
        if key not in fields or fields[key] is None:
            continue
        value = fields[key]
        if key in ("headers", "events"):
            value = json.dumps(value)
        elif key == "enabled":
            value = int(value)
        columns.append(f"{key} = ?")
        values.append(value)
    if not columns:
        return get_hook(hook_id)
    columns.append("updated_at = ?")
    values.extend([request_models.now_iso(), hook_id])
    db.execute(f"UPDATE jf_download_hook SET {', '.join(columns)} WHERE id = ?", tuple(values))
    return get_hook(hook_id)


def delete_hook(hook_id: int):
    db.execute("DELETE FROM jf_download_hook WHERE id = ?", (hook_id,))


def list_enabled_for_event(event: str) -> list[dict]:
    """Enabled hooks subscribed to this event. An empty filter means all events.

    The same rule the notification channels use, and the same reason the filter
    runs in Python: matching inside a JSON column would tie the panel to the
    SQLite JSON1 extension being compiled in.
    """
    hooks = [_row_to_hook(r) for r in
             db.query("SELECT * FROM jf_download_hook WHERE enabled = 1 ORDER BY id")]
    return [h for h in hooks if not h["events"] or event in h["events"]]


# ── Payload ───────────────────────────────────────────────────────────────────

def job_tokens(job) -> dict:
    """The substitutions a body template can use.

    Every value is a string, empty rather than absent when unknown: a job
    restored from schedule.json carries no year, season or episode number, and a
    template must render rather than blow up inside a download.
    """
    return {
        "title": str(getattr(job, "title", "") or ""),
        "path": str(getattr(job, "output_path", "") or ""),
        "status": str(getattr(job, "status", "") or ""),
        "type": str(getattr(job, "type", "") or ""),
        "season": str(getattr(job, "season", "") or ""),
        "episode": str(getattr(job, "episode_number", "") or ""),
        "year": str(getattr(job, "year", "") or ""),
        "error": str(getattr(job, "error", "") or ""),
    }


def render_body(template: str, tokens: dict) -> str:
    """Substitute {tokens} in a body, escaping each value for JSON.

    Not str.format: a JSON body is full of braces, and a title containing a
    quote would produce a payload the other end rejects. Values go through
    json.dumps and lose their surrounding quotes, so `"title": "{title}"` in the
    template stays valid whatever the title is.
    """
    if not template:
        return json.dumps(tokens)

    rendered = template
    for key, value in tokens.items():
        rendered = rendered.replace("{" + key + "}", json.dumps(value)[1:-1])
    return rendered


# ── Delivery ──────────────────────────────────────────────────────────────────

def fire(hook: dict, tokens: dict) -> tuple[bool, int | None]:
    """Send one hook. Returns (ok, status code); never raises, never returns a body."""
    method = (hook.get("method") or "POST").upper()
    if method not in ALLOWED_METHODS:
        logger.warning("Hook %s has an unsupported method %r", hook.get("name"), method)
        return False, None

    body = render_body(hook.get("body_template") or "", tokens)
    headers = dict(hook.get("headers") or {})
    headers.setdefault("content-type", "application/json")

    try:
        response = requests.request(
            method,
            hook["url"],
            data=None if method == "GET" else body.encode("utf-8"),
            headers=headers,
            timeout=_TIMEOUT,
        )
    except Exception as exc:
        # The URL is not logged: it carries the token.
        logger.warning("Hook %s failed: %s", hook.get("name"), type(exc).__name__)
        return False, None

    if not response.ok:
        logger.warning("Hook %s returned HTTP %d", hook.get("name"), response.status_code)
    return response.ok, response.status_code


def refresh_jellyfin_library() -> tuple[bool, int | None]:
    """Ask Jellyfin to scan, using the credentials already stored for login."""
    from app.auth import models as auth_models

    url = (auth_models.get_setting(auth_models.SETTING_JELLYFIN_URL) or "").strip()
    api_key = (auth_models.get_setting(auth_models.SETTING_JELLYFIN_API_KEY) or "").strip()
    if not url or not api_key:
        logger.info("Library refresh skipped: Jellyfin is not connected")
        return False, None

    try:
        response = requests.post(
            f"{url.rstrip('/')}/Library/Refresh",
            headers={"X-Emby-Token": api_key},
            timeout=_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("Jellyfin library refresh failed: %s", type(exc).__name__)
        return False, None

    if not response.ok:
        logger.warning("Jellyfin library refresh returned HTTP %d", response.status_code)
    return response.ok, response.status_code


# ── The listener ──────────────────────────────────────────────────────────────

def _notify_failure(hook_name: str, status: int | None) -> None:
    """A hook that fails silently is worse than not having one."""
    try:
        from app.requests import notify as notify_module

        detail = f"HTTP {status}" if status else "nessuna risposta"
        notify_module.notify(
            notify_module.HOOK_FAILED,
            f"L'hook «{hook_name}» non è andato a buon fine ({detail}).",
            notify_module.settings_manager_ids(),
            title="Hook post-download",
            panel_wide=True,
        )
    except Exception:
        logger.exception("Cannot announce the hook failure")


def on_job_finished(job) -> None:
    """Fire the configured hooks for one finished download.

    Runs on a worker thread outside the download semaphore, which is why the
    blocking requests here are fine, and JobManager isolates each listener's
    exceptions — so a broken hook cannot take a download down with it.
    """
    status = getattr(job, "status", None)
    if status not in HOOK_EVENTS:
        return

    if status == "done":
        # Sonarr/Radarr first: when one of them claims the file, it moves it
        # out of the panel's own library into its own folder before Jellyfin
        # or Plex ever look. Either of those scanning first would find
        # nothing yet at the file's final location, and not look there
        # again once the move actually happens a moment later.
        _maybe_refresh_sonarr(job)
        _maybe_refresh_radarr(job)
        _maybe_refresh_jellyfin()
        _maybe_refresh_plex()

    tokens = job_tokens(job)
    for hook in list_enabled_for_event(status):
        try:
            ok, code = fire(hook, tokens)
        except Exception:
            logger.exception("Hook %s raised", hook.get("name"))
            ok, code = False, None
        if not ok:
            _notify_failure(hook.get("name") or "senza nome", code)


def _maybe_refresh_jellyfin() -> None:
    from app.config import get_settings

    if not get_settings().get("jellyfin_refresh_on_download"):
        return
    refresh_jellyfin_library()


def _maybe_refresh_plex() -> None:
    from app.config import get_settings
    from app.integrations import plex

    if not get_settings().get("plex_refresh_on_download"):
        return
    plex.refresh_libraries()


def _maybe_refresh_sonarr(job) -> None:
    """Rescan, or — when this episode is one Sonarr itself is missing, found
    by title and season/episode the same way the sync does — move it into
    Sonarr's own folder for that series and rescan just that series.

    Works for *any* finished episode, not only ones the sync submitted: a
    manually downloaded episode that happens to be on Sonarr's own missing
    list gets the same handoff.
    """
    from app.config import get_settings
    from app.integrations import sonarr

    if not get_settings().get("sonarr_refresh_on_download"):
        return

    media_label = getattr(job, "media_label", None)
    season = getattr(job, "season", None)
    episode_number = getattr(job, "episode_number", None)
    output_path = getattr(job, "output_path", None)
    if job.type == "episode" and media_label and season is not None and episode_number and output_path:
        episode = sonarr.find_missing_episode(media_label, season, episode_number)
        if episode is None:
            logger.info(
                "Sonarr: no missing match for «%s» S%02dE%s — plain rescan only",
                media_label, season, episode_number,
            )
        elif sonarr.import_into_library(output_path, episode):
            logger.info(
                "Sonarr: imported «%s» S%02dE%s into %s",
                media_label, season, episode_number, episode["series"]["path"],
            )
            return
        else:
            logger.warning(
                "Sonarr: found «%s» S%02dE%s but could not move the file — plain rescan only",
                media_label, season, episode_number,
            )

    sonarr.rescan_series()


def _maybe_refresh_radarr(job) -> None:
    """Rescan, or — when this film is one Radarr itself is missing, matched
    by the exact tmdb_id rather than a guess — move it into Radarr's own
    folder for that movie and rescan just that movie.

    Works for *any* finished film, not only ones the sync submitted: a
    manually downloaded film that happens to be on Radarr's own missing list
    gets the same handoff.
    """
    from app.config import get_settings
    from app.integrations import radarr

    if not get_settings().get("radarr_refresh_on_download"):
        return

    tmdb_id = getattr(job, "tmdb_id", None)
    output_path = getattr(job, "output_path", None)
    if job.type == "film" and tmdb_id and output_path:
        movie = radarr.find_missing_movie(tmdb_id)
        if movie is None:
            logger.info("Radarr: no missing match for tmdb_id %s — plain rescan only", tmdb_id)
        elif radarr.import_into_library(output_path, movie):
            logger.info("Radarr: imported tmdb_id %s into %s", tmdb_id, movie["path"])
            return
        else:
            logger.warning(
                "Radarr: found tmdb_id %s but could not move the file — plain rescan only", tmdb_id,
            )

    radarr.rescan_movie()


def register_hook_listener():
    """Register once. Idempotent, like the other two listeners."""
    global _listener_registered
    with _listener_lock:
        if _listener_registered:
            return
        from app.jobs import job_manager

        job_manager.add_listener(on_job_finished)
        _listener_registered = True
