"""Matching a Sonarr/Radarr "wanted" item to a title on the source.

Radarr carries a TMDB id, which ``app.core.metadata`` already reads from the
title page (the same id used for the stream fallback — see CLAUDE.md on
``metadata.cached_tmdb_id``). Comparing the two is an exact match or nothing,
never a guess, when Radarr gives one.

Sonarr carries no id this source exposes (TVDB only), so a series match is
title+year similarity above a high threshold; anything lower is left for a
human to place by hand rather than risked. Downloading the wrong film or
series is not a recoverable mistake the way a retried download is — see the
"never substitute" rules in CLAUDE.md.
"""

import difflib
import logging

from app.core import metadata, page
from app.requests import resolver

logger = logging.getLogger(__name__)

# Below this, a title+year match is not trusted on its own.
SERIES_MATCH_THRESHOLD = 0.88
YEAR_MISMATCH_PENALTY = 0.15


def _normalize(title: str) -> str:
    return "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in title).strip()


def _title_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _candidate_year(candidate: dict) -> str | None:
    date = candidate.get("release_date") or candidate.get("last_air_date") or ""
    return date[:4] if len(date) >= 4 else None


def _best_fuzzy(results: list[dict], title: str, year: str | None) -> dict | None:
    best, best_score = None, 0.0
    for candidate in results:
        score = _title_score(title, candidate.get("name") or "")
        candidate_year = _candidate_year(candidate)
        if year and candidate_year and candidate_year != str(year):
            score -= YEAR_MISMATCH_PENALTY
        if score > best_score:
            best, best_score = candidate, score
    if best is not None and best_score >= SERIES_MATCH_THRESHOLD:
        return best
    return None


def match_film(title: str, year: str | None, tmdb_id: int | None, domain: str) -> dict | None:
    """A Radarr movie against the source.

    An exact ``tmdb_id`` match when Radarr gives one — and nothing, not a
    fuzzy fallback, when a tmdb_id was given and no candidate carries it: a
    wrong film with the right-sounding title is worse than no match at all.
    Only falls back to title+year similarity when Radarr itself has no
    tmdb_id to confirm against.
    """
    results = page.search(title, domain, media_type="movie")
    if not results:
        return None

    if tmdb_id:
        version = page.get_domain_version(domain) or ""
        for candidate in results:
            try:
                meta = metadata.title_metadata(
                    resolver.FILM, candidate["id"], candidate.get("slug") or "", version
                )
            except Exception:
                continue
            if meta.get("tmdb_id") == tmdb_id:
                return candidate
        return None

    return _best_fuzzy(results, title, year)


def match_series(title: str, year: str | None, domain: str) -> dict | None:
    """A Sonarr series against the source. Best-effort: no id to confirm it with."""
    results = page.search(title, domain, media_type="tv")
    return _best_fuzzy(results, title, year)
