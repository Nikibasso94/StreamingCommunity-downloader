"""The periodic Sonarr/Radarr sync: ledger dedup, direct downloads in open
mode, and the request queue when accounts are enabled.

Nothing here should download anything the request pipeline would not
otherwise download — ``source`` already gives every test a fake film/tv
source and a throwaway library, the same fixture app.watches' own poller
tests use for exactly this reason.
"""

import pytest

from app.integrations import matching, radarr, sonarr, sync
from app.requests import models as request_models
from tests.conftest import enable_open_mode, make_user

SONARR_EPISODE = {
    "id": 501, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 4,
    "series": {"title": "Una Serie", "year": 2019},
}

RADARR_MOVIE = {"id": 601, "title": "Un Film", "year": 2020, "tmdbId": 12345}


def _series_candidate():
    return {"id": 77, "slug": "una-serie", "poster": None}


def _film_candidate():
    return {"id": 88, "slug": "un-film", "poster": None}


@pytest.fixture
def open_panel(client, source):
    enable_open_mode()
    return source


# ── Ledger ─────────────────────────────────────────────────────────────────────

def test_record_seen_round_trips(client):
    sync.record_seen("sonarr", "1", "needs_review", "Qualcosa")

    row = sync.get_seen("sonarr", "1")

    assert row["status"] == "needs_review"
    assert row["title"] == "Qualcosa"


def test_record_seen_overwrites_the_same_key(client):
    sync.record_seen("sonarr", "1", "needs_review", "Qualcosa")
    sync.record_seen("sonarr", "1", "downloading", "Qualcosa")

    assert sync.get_seen("sonarr", "1")["status"] == "downloading"


def test_review_items_only_lists_unplaced_outcomes(client):
    sync.record_seen("sonarr", "1", "needs_review", "A")
    sync.record_seen("sonarr", "2", "not_found", "B")
    sync.record_seen("sonarr", "3", "downloading", "C")

    assert {i["external_key"] for i in sync.review_items("sonarr")} == {"1", "2"}


# ── Sonarr: open mode ────────────────────────────────────────────────────────

def test_sonarr_item_with_no_match_is_parked_for_review(client, open_panel, monkeypatch):
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: None)

    outcome = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert outcome == "needs_review"
    assert sync.get_seen("sonarr", "501")["status"] == "needs_review"


def test_sonarr_item_with_a_match_downloads_directly_in_open_mode(
    client, open_panel, stub_jobs, monkeypatch,
):
    open_panel.episodes.append({"id": 904, "n": "4", "name": "Episodio 4"})
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: _series_candidate())

    outcome = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert outcome == "downloading"
    assert [name for name, _, _ in stub_jobs] == ["submit_episode"]
    _, _, kwargs = stub_jobs[0]
    assert kwargs["strict_audio"] is True
    assert kwargs["user_id"] is None
    assert sync.get_seen("sonarr", "501")["status"] == "downloading"


def test_a_numbering_mismatch_is_skipped_quietly_not_reviewed_or_retried(
    client, open_panel, monkeypatch,
):
    """The series matches; the source just has no such season/episode under
    that numbering (common for long-running shows Sonarr and the source split
    into seasons differently). The automatic cycle must not park this for
    review or keep retrying it — there is nothing a human would change."""
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: _series_candidate())
    # open_panel's fake source has episodes 1-3; SONARR_EPISODE asks for "4".

    outcome = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert outcome == "numbering_mismatch"
    assert sync.get_seen("sonarr", "501")["status"] == "numbering_mismatch"
    assert sync.review_items("sonarr") == []

    match_calls = []
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: match_calls.append(1))
    again = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert again == "numbering_mismatch"
    assert match_calls == []  # settled: not retried


def test_resolving_the_same_mismatch_by_hand_still_raises(client, open_panel, monkeypatch):
    """Automatic skips it quietly; a human acting on it deliberately still
    gets the real error, because there the message is useful, not noise."""
    monkeypatch.setattr(sonarr, "get_episode", lambda eid: SONARR_EPISODE if eid == "501" else None)

    with pytest.raises(sync.EpisodeNotFoundError):
        sync.resolve_review_item("sonarr", "501", _series_candidate())


def test_a_settled_sonarr_item_is_not_reprocessed(client, open_panel, monkeypatch):
    sync.record_seen("sonarr", "501", "downloading", "Una Serie")
    match_calls = []
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: match_calls.append(1))

    outcome = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert outcome == "downloading"
    assert match_calls == []  # never even tried to match again


# ── Radarr: open mode ────────────────────────────────────────────────────────

def test_radarr_item_with_no_match_and_a_tmdb_id_is_marked_not_found(client, open_panel, monkeypatch):
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: None)

    outcome = sync.process_radarr_item("example.test", RADARR_MOVIE)

    assert outcome == "needs_review"
    assert sync.get_seen("radarr", "601")["status"] == "not_found"


def test_radarr_item_with_a_match_downloads_directly_in_open_mode(
    client, open_panel, stub_jobs, monkeypatch,
):
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())

    outcome = sync.process_radarr_item("example.test", RADARR_MOVIE)

    assert outcome == "downloading"
    assert [name for name, _, _ in stub_jobs] == ["submit_film"]
    _, _, kwargs = stub_jobs[0]
    assert kwargs["strict_audio"] is True
    assert kwargs["user_id"] is None


# ── With accounts enabled: the request queue ────────────────────────────────

def test_without_a_managed_by_user_the_sync_leaves_the_item_for_review(client, source, monkeypatch):
    # Accounts enabled (not open mode), but nobody configured to own synced
    # requests: the sync must not pick a user on its own.
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())

    outcome = sync.process_radarr_item("example.test", RADARR_MOVIE)

    assert outcome == "submit_failed"
    assert request_models.list_all() == []


def test_with_a_managed_by_user_the_sync_queues_and_auto_approves(
    client, source, stub_jobs, monkeypatch,
):
    from app.config import get_settings, save_settings

    user = make_user("arr-owner", "jf-arr-owner-id", 0)
    save_settings({**get_settings(), "arr_managed_by_user_id": user.id})
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())

    outcome = sync.process_radarr_item("example.test", RADARR_MOVIE)

    assert outcome == "auto_approved"
    requests = request_models.list_all()
    assert len(requests) == 1
    assert requests[0].requested_by == user.id


# ── The job carries what app.downloads_hooks needs to find it on Radarr ────
#
# Handing a finished download to Radarr/Sonarr's own import is
# app.downloads_hooks' job, done the same way for every job regardless of
# where it came from — see tests/test_download_hooks_arr_match.py. All this
# module has to get right is tagging the job with Radarr's tmdb_id, so that
# code can look the film back up.

def test_a_direct_film_download_carries_radarr_s_tmdb_id(
    client, open_panel, stub_jobs, monkeypatch,
):
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())

    sync.process_radarr_item("example.test", RADARR_MOVIE)

    _, _, kwargs = stub_jobs[0]
    assert kwargs["tmdb_id"] == RADARR_MOVIE["tmdbId"]


def test_resolving_a_radarr_item_by_hand_also_carries_its_tmdb_id(
    client, open_panel, stub_jobs, monkeypatch,
):
    monkeypatch.setattr(radarr, "get_movie", lambda mid: RADARR_MOVIE if mid == "601" else None)

    sync.resolve_review_item("radarr", "601", _film_candidate())

    _, _, kwargs = stub_jobs[0]
    assert kwargs["tmdb_id"] == RADARR_MOVIE["tmdbId"]


# ── The cycle respects its settings ─────────────────────────────────────────

def test_run_sync_cycle_does_nothing_when_both_are_disabled(client, open_panel, monkeypatch):
    calls = []
    monkeypatch.setattr(sonarr, "wanted_missing", lambda: calls.append("sonarr") or [])
    monkeypatch.setattr(radarr, "wanted_missing", lambda: calls.append("radarr") or [])

    sync.run_sync_cycle()

    assert calls == []


def test_run_sync_cycle_only_calls_the_enabled_service(client, open_panel, monkeypatch):
    from app.config import get_settings, save_settings

    save_settings({**get_settings(), "sonarr_sync_wanted": True})
    monkeypatch.setattr(sonarr, "is_connected", lambda: True)
    monkeypatch.setattr(radarr, "is_connected", lambda: True)
    calls = []
    monkeypatch.setattr(sonarr, "wanted_missing", lambda: calls.append("sonarr") or [])
    monkeypatch.setattr(radarr, "wanted_missing", lambda: calls.append("radarr") or [])

    sync.run_sync_cycle()

    assert calls == ["sonarr"]


# ── Manual resolution of a review item ──────────────────────────────────────

def test_resolving_a_sonarr_item_re_reads_it_from_sonarr_and_downloads(
    client, open_panel, stub_jobs, monkeypatch,
):
    open_panel.episodes.append({"id": 904, "n": "4", "name": "Episodio 4"})
    monkeypatch.setattr(sonarr, "get_episode", lambda eid: SONARR_EPISODE if eid == "501" else None)
    sync.record_seen("sonarr", "501", "needs_review", "Una Serie")

    outcome = sync.resolve_review_item("sonarr", "501", _series_candidate())

    assert outcome == "downloading"
    assert [name for name, _, _ in stub_jobs] == ["submit_episode"]
    assert sync.get_seen("sonarr", "501")["status"] == "downloading"
    assert sync.review_items("sonarr") == []


def test_resolving_a_radarr_item_re_reads_it_from_radarr_and_downloads(
    client, open_panel, stub_jobs, monkeypatch,
):
    monkeypatch.setattr(radarr, "get_movie", lambda mid: RADARR_MOVIE if mid == "601" else None)
    sync.record_seen("radarr", "601", "not_found", "Un Film")

    outcome = sync.resolve_review_item("radarr", "601", _film_candidate())

    assert outcome == "downloading"
    assert [name for name, _, _ in stub_jobs] == ["submit_film"]
    assert sync.review_items("radarr") == []


def test_resolving_a_gone_sonarr_episode_raises(client, open_panel, monkeypatch):
    monkeypatch.setattr(sonarr, "get_episode", lambda eid: None)

    with pytest.raises(RuntimeError):
        sync.resolve_review_item("sonarr", "501", _series_candidate())


def test_dismissing_an_item_removes_it_from_the_review_list(client):
    sync.record_seen("sonarr", "501", "needs_review", "Una Serie")

    sync.dismiss_review_item("sonarr", "501")

    assert sync.review_items("sonarr") == []
    assert sync.get_seen("sonarr", "501")["status"] == "dismissed"


def test_a_dismissed_item_is_not_reprocessed_by_the_next_cycle(client, open_panel, monkeypatch):
    sync.record_seen("sonarr", "501", "dismissed", "Una Serie")
    match_calls = []
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: match_calls.append(1))

    outcome = sync.process_sonarr_item("example.test", SONARR_EPISODE)

    assert outcome == "dismissed"
    assert match_calls == []
