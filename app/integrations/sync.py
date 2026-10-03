"""Periodic sync of Sonarr/Radarr's "wanted" lists into downloads here.

Shaped after ``app.watches.poller``: nothing here downloads anything the
request pipeline would not otherwise download. In open mode it submits a job
directly, the same bypass ``watches.poller._submit_direct`` uses, because the
implicit "Pannello" user has no row in ``jf_user`` and ``jf_request.requested_by``
could not reference it. With accounts enabled it goes through
``app.requests.service`` like everything else a human could have asked for,
owned by whichever user an administrator configured to run these.

Every item Sonarr/Radarr call "wanted" is remembered in ``jf_arr_sync_seen``
by *their* id, with the outcome — so a title the matcher could not place is
not re-searched every cycle, and a download already placed is not
resubmitted. Unmatched items stay visible through ``review_items()`` instead
of being dropped silently.

A download this module places still downloads straight to the panel's own
library, exactly like a manual one — this module's only job is deciding
*what* to download and placing the job, carrying the tmdb_id (films) or
title/season/episode (episodes) that identify it. Handing the finished file
to Sonarr/Radarr's own import, when it is something they are missing, is
``app.downloads_hooks``' job, and it is the same code path for a download
this module started and one a human started by hand.
"""

import asyncio
import logging

from app import db
from app.auth import models as auth_models
from app.config import get_settings
from app.integrations import matching, radarr, sonarr
from app.requests import models as request_models, resolver

logger = logging.getLogger(__name__)

POLL_INTERVAL_DEFAULT_MINUTES = 240
POLL_INTERVAL_MIN_MINUTES = 15

# No per-item language choice here, unlike a watch or a manual request: there
# is nobody on the other end to ask. Same defaults app.jobs falls back to.
DEFAULT_AUDIO = ["ita"]
DEFAULT_SUBTITLES = ["ita", "eng"]

# Outcomes that must not be re-attempted every cycle: the sync already placed
# a download, or the title is already in the library.
SETTLED_STATUSES = (
    "downloading", "auto_approved", "queued", "already_in_library", "dismissed",
    "numbering_mismatch",
)


class EpisodeNotFoundError(RuntimeError):
    """The series matched, but the source has no episode numbered the way
    Sonarr numbers it.

    Long-running shows with hundreds of short episodes (soaps, daily
    sitcoms) are often split into seasons differently by Sonarr's catalogue
    and by this source — "season 1" can mean completely different spans of
    episodes on each side. There is no mapping between the two to resolve
    this automatically, so the automatic cycle skips the episode rather than
    erroring every run; see its one caller in ``process_sonarr_item``. A
    human resolving the same episode by hand still sees this raised, because
    there the message is useful rather than noise.
    """


def _interval_seconds() -> int:
    try:
        minutes = int(get_settings().get("arr_sync_interval_minutes", POLL_INTERVAL_DEFAULT_MINUTES))
    except (TypeError, ValueError):
        minutes = POLL_INTERVAL_DEFAULT_MINUTES
    return max(minutes, POLL_INTERVAL_MIN_MINUTES) * 60


# ── Ledger ─────────────────────────────────────────────────────────────────────

def get_seen(service: str, external_key: str) -> dict | None:
    row = db.query_one(
        "SELECT * FROM jf_arr_sync_seen WHERE service = ? AND external_key = ?",
        (service, external_key),
    )
    return dict(row) if row else None


def record_seen(service: str, external_key: str, status: str, title: str,
                 request_id: int | None = None) -> None:
    timestamp = request_models.now_iso()
    db.execute(
        "INSERT INTO jf_arr_sync_seen(service, external_key, status, request_id, title, last_checked_at) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(service, external_key) DO UPDATE SET "
        "status = excluded.status, request_id = excluded.request_id, "
        "title = excluded.title, last_checked_at = excluded.last_checked_at",
        (service, external_key, status, request_id, title, timestamp),
    )


def review_items(service: str | None = None) -> list[dict]:
    """Items the matcher could not place on its own, newest first."""
    if service:
        rows = db.query(
            "SELECT * FROM jf_arr_sync_seen WHERE service = ? "
            "AND status IN ('needs_review', 'not_found') ORDER BY last_checked_at DESC",
            (service,),
        )
    else:
        rows = db.query(
            "SELECT * FROM jf_arr_sync_seen WHERE status IN ('needs_review', 'not_found') "
            "ORDER BY last_checked_at DESC"
        )
    return [dict(r) for r in rows]


# ── Placing a match ──────────────────────────────────────────────────────────

def _managed_by_user_id() -> int | None:
    raw = get_settings().get("arr_managed_by_user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _draft_request(media_type: str, candidate: dict, season, episode_number,
                    title: str, year: str | None) -> request_models.Request:
    """A throwaway record shaped like a request, only to ask the resolver
    whether the file is already in the library. Never stored."""
    return request_models.Request(
        id=0, content_key="", source="streamingcommunity", media_type=media_type,
        external_id=candidate["id"], slug=candidate.get("slug"), title=title, year=year,
        poster=candidate.get("poster"), season=season, episode_number=episode_number,
        anime_type=None, audio_languages=DEFAULT_AUDIO, subtitle_languages=DEFAULT_SUBTITLES,
        available_snapshot=None, status=request_models.PENDING, problem=None, denial_reason=None,
        requested_by=0, decided_by=None, decided_at=None, job_id=None, output_path=None,
        created_at="", updated_at="",
    )


def _submit_direct_episode(domain: str, candidate: dict, season, episode_number,
                            title: str, year: str | None) -> str:
    from app.core.page import get_domain_version
    from app.core.tv import get_info_season, get_token
    from app.jobs import job_manager

    tv_id = int(candidate["id"])
    version = get_domain_version(domain) or ""
    token = get_token(tv_id, domain)
    episodes = get_info_season(tv_id, candidate.get("slug") or "", domain, version, token, season or 1)
    for index, episode in enumerate(episodes or []):
        if str(episode["n"]) == str(episode_number):
            return job_manager.submit_episode(
                tv_id, episodes, index, domain, token, title, season or 1,
                year=year, audio_languages=DEFAULT_AUDIO, subtitle_languages=DEFAULT_SUBTITLES,
                strict_audio=True, user_id=None,
            )
    raise EpisodeNotFoundError(f"episodio S{season}E{episode_number} non trovato per «{title}»")


def _submit_direct_film(domain: str, candidate: dict, title: str, year: str | None,
                         tmdb_id: int | None) -> str:
    from app.jobs import job_manager

    return job_manager.submit_film(
        int(candidate["id"]), title, domain, year=year,
        audio_languages=DEFAULT_AUDIO, subtitle_languages=DEFAULT_SUBTITLES,
        strict_audio=True, user_id=None, tmdb_id=tmdb_id,
    )


def _queue_request(media_type: str, candidate: dict, season, episode_number,
                    title: str, year: str | None) -> str:
    from app.requests import service as requests_service

    managed_by = _managed_by_user_id()
    if managed_by is None:
        raise RuntimeError("nessun utente configurato per gestire le richieste automatiche")

    request, created = requests_service.create_request(
        requested_by=managed_by,
        source="streamingcommunity",
        media_type=media_type,
        external_id=candidate["id"],
        title=title,
        slug=candidate.get("slug"),
        year=year,
        poster=candidate.get("poster"),
        season=season,
        episode_number=episode_number,
        audio_languages=DEFAULT_AUDIO,
        subtitle_languages=DEFAULT_SUBTITLES,
    )
    if request.status in (request_models.AVAILABLE, request_models.COMPLETED):
        return "already_in_library"
    if created and request.status == request_models.PENDING:
        requests_service.approve(request.id, decided_by=managed_by)
        return "auto_approved"
    return "queued"


def _place(media_type: str, domain: str, candidate: dict, season, episode_number,
           title: str, year: str | None, tmdb_id: int | None = None) -> str:
    draft = _draft_request(media_type, candidate, season, episode_number, title, year)
    try:
        if resolver.is_in_library(draft):
            return "already_in_library"
    except Exception:
        logger.exception("Library check failed for %s", title)

    if auth_models.runtime_open_mode():
        if media_type == resolver.EPISODE:
            _submit_direct_episode(domain, candidate, season, episode_number, title, year)
        else:
            _submit_direct_film(domain, candidate, title, year, tmdb_id)
        return "downloading"

    return _queue_request(media_type, candidate, season, episode_number, title, year)


# ── Per-item processing ────────────────────────────────────────────────────────

def process_sonarr_item(domain: str, record: dict) -> str:
    external_key = str(record.get("id"))
    series = record.get("series") or {}
    title = series.get("title") or record.get("title") or "?"
    year = str(series["year"]) if series.get("year") else None
    season = record.get("seasonNumber")
    episode_number = record.get("episodeNumber")

    existing = get_seen("sonarr", external_key)
    if existing and existing["status"] in SETTLED_STATUSES:
        return existing["status"]

    try:
        candidate = matching.match_series(title, year, domain)
    except Exception:
        logger.exception("Sonarr matching failed for %s", title)
        candidate = None

    if candidate is None:
        record_seen("sonarr", external_key, "needs_review", title)
        return "needs_review"

    try:
        outcome = _place(resolver.EPISODE, domain, candidate, season, str(episode_number), title, year)
    except EpisodeNotFoundError:
        # The series matched; the source just does not have this exact
        # season/episode under that numbering. Skipped quietly rather than
        # retried every cycle or parked for review — there is nothing a human
        # resolving it differently would change, since the series is already
        # right. See EpisodeNotFoundError.
        logger.info("Sonarr sync: no S%sE%s for «%s» on the source", season, episode_number, title)
        outcome = "numbering_mismatch"
    except Exception:
        logger.exception("Sonarr sync failed to submit %s S%sE%s", title, season, episode_number)
        outcome = "submit_failed"
    record_seen("sonarr", external_key, outcome, title)
    return outcome


def process_radarr_item(domain: str, record: dict) -> str:
    external_key = str(record.get("id"))
    title = record.get("title") or "?"
    year = str(record["year"]) if record.get("year") else None
    tmdb_id = record.get("tmdbId")

    existing = get_seen("radarr", external_key)
    if existing and existing["status"] in SETTLED_STATUSES:
        return existing["status"]

    try:
        candidate = matching.match_film(title, year, tmdb_id, domain)
    except Exception:
        logger.exception("Radarr matching failed for %s", title)
        candidate = None

    if candidate is None:
        # A tmdb_id with no match is worth distinguishing from "no id to go
        # on at all" in the review list, but both need a human.
        record_seen("radarr", external_key, "not_found" if tmdb_id else "needs_review", title)
        return "needs_review"

    try:
        outcome = _place(resolver.FILM, domain, candidate, None, None, title, year, tmdb_id)
    except Exception:
        logger.exception("Radarr sync failed to submit %s", title)
        outcome = "submit_failed"
    record_seen("radarr", external_key, outcome, title)
    return outcome


# ── Manual resolution of a review item ──────────────────────────────────────
#
# When the matcher could not place something on its own, a human can pick the
# right title from a search instead — reusing _place() so the result is
# downloaded (or handed to Sonarr/Radarr's own import) exactly the way an
# automatic match would be. The episode/movie is re-read from Sonarr/Radarr by
# id rather than trusting anything stored in the ledger, the same reason every
# other resolution in this panel re-reads the source rather than a snapshot.

def resolve_review_item(service: str, external_key: str, candidate: dict) -> str:
    domain = resolver.current_domain()

    if service == "sonarr":
        record = sonarr.get_episode(external_key)
        if record is None:
            raise RuntimeError("Episodio non più trovato su Sonarr")
        series = record.get("series") or {}
        title = series.get("title") or record.get("title") or "?"
        year = str(series["year"]) if series.get("year") else None
        season = record.get("seasonNumber")
        episode_number = record.get("episodeNumber")
        outcome = _place(resolver.EPISODE, domain, candidate, season, str(episode_number), title, year)
    elif service == "radarr":
        record = radarr.get_movie(external_key)
        if record is None:
            raise RuntimeError("Film non più trovato su Radarr")
        title = record.get("title") or "?"
        year = str(record["year"]) if record.get("year") else None
        outcome = _place(resolver.FILM, domain, candidate, None, None, title, year, record.get("tmdbId"))
    else:
        raise ValueError(f"Servizio sconosciuto: {service!r}")

    record_seen(service, external_key, outcome, title)
    return outcome


def dismiss_review_item(service: str, external_key: str) -> None:
    """Acknowledge an item without downloading anything. ``dismissed`` is a
    settled status, so the next sync cycle leaves it alone instead of putting
    it straight back in the review list."""
    existing = get_seen(service, external_key)
    title = existing["title"] if existing else "?"
    record_seen(service, external_key, "dismissed", title)


# ── The cycle ──────────────────────────────────────────────────────────────────

def run_sync_cycle() -> dict:
    """One pass over whichever of Sonarr/Radarr is enabled. Never raises: a
    bad item or an unreachable service must not stop the other half, and the
    loop above must keep running."""
    settings = get_settings()
    summary = {"sonarr": 0, "radarr": 0}

    try:
        domain = resolver.current_domain()
    except Exception:
        logger.info("Arr sync skipped: no source domain configured")
        return summary

    if settings.get("sonarr_sync_wanted") and sonarr.is_connected():
        for record in sonarr.wanted_missing():
            try:
                process_sonarr_item(domain, record)
                summary["sonarr"] += 1
            except Exception:
                logger.exception("Sonarr sync item failed")

    if settings.get("radarr_sync_wanted") and radarr.is_connected():
        for record in radarr.wanted_missing():
            try:
                process_radarr_item(domain, record)
                summary["radarr"] += 1
            except Exception:
                logger.exception("Radarr sync item failed")

    return summary


async def arr_sync_loop():
    """Background task started from the app lifespan, alongside the watch poller."""
    while True:
        await asyncio.sleep(_interval_seconds())
        try:
            await asyncio.to_thread(run_sync_cycle)
        except Exception:
            logger.exception("Arr sync cycle failed")
