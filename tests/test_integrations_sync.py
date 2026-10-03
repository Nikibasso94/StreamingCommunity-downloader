"""The periodic Sonarr/Radarr sync: ledger dedup, direct downloads in open
mode, and the request queue when accounts are enabled.

Nothing here should download anything the request pipeline would not
otherwise download — ``source`` already gives every test a fake film/tv
source and a throwaway library, the same fixture app.watches' own poller
tests use for exactly this reason.
"""

from types import SimpleNamespace

import pytest

from app.integrations import matching, radarr, sonarr, sync
from app.requests import models as request_models
from tests.conftest import enable_open_mode, make_user


@pytest.fixture(autouse=True)
def _clear_pending_imports():
    """Module-level tracking dict; a leaked entry would cross test boundaries
    since job ids from ``stub_jobs`` restart at "job-1" every test."""
    sync._pending_imports.clear()
    yield
    sync._pending_imports.clear()

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


# ── Handoff to Sonarr/Radarr's own import ───────────────────────────────────

def test_a_configured_import_dir_becomes_the_download_s_output_dir(
    client, open_panel, stub_jobs, monkeypatch,
):
    open_panel.episodes.append({"id": 904, "n": "4", "name": "Episodio 4"})
    sonarr.set_import_dir("/staging/sonarr")
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: _series_candidate())

    sync.process_sonarr_item("example.test", SONARR_EPISODE)

    _, _, kwargs = stub_jobs[0]
    assert kwargs["output_dir"] == "/staging/sonarr"


def test_without_an_import_dir_the_download_uses_the_default_output(
    client, open_panel, stub_jobs, monkeypatch,
):
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())

    sync.process_radarr_item("example.test", RADARR_MOVIE)

    _, _, kwargs = stub_jobs[0]
    assert kwargs["output_dir"] is None


def test_a_finished_tracked_job_triggers_sonarr_import_scan(
    client, open_panel, stub_jobs, monkeypatch,
):
    open_panel.episodes.append({"id": 904, "n": "4", "name": "Episodio 4"})
    sonarr.set_import_dir("/staging/sonarr")
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: _series_candidate())
    calls = []
    monkeypatch.setattr(sonarr, "import_scan", lambda path: calls.append(path) or True)

    sync.process_sonarr_item("example.test", SONARR_EPISODE)
    sync.on_job_finished(SimpleNamespace(job_id="job-1", status="done"))

    assert calls == ["/staging/sonarr"]


def test_a_finished_tracked_job_triggers_radarr_import_scan(
    client, open_panel, stub_jobs, monkeypatch,
):
    radarr.set_import_dir("/staging/radarr")
    monkeypatch.setattr(matching, "match_film", lambda *a, **k: _film_candidate())
    calls = []
    monkeypatch.setattr(radarr, "import_scan", lambda path: calls.append(path) or True)

    sync.process_radarr_item("example.test", RADARR_MOVIE)
    sync.on_job_finished(SimpleNamespace(job_id="job-1", status="done"))

    assert calls == ["/staging/radarr"]


def test_an_untracked_job_never_triggers_an_import_scan(client, monkeypatch):
    calls = []
    monkeypatch.setattr(sonarr, "import_scan", lambda path: calls.append(path) or True)

    sync.on_job_finished(SimpleNamespace(job_id="some-other-job", status="done"))

    assert calls == []


def test_a_failed_tracked_job_does_not_trigger_an_import_scan(
    client, open_panel, stub_jobs, monkeypatch,
):
    open_panel.episodes.append({"id": 904, "n": "4", "name": "Episodio 4"})
    sonarr.set_import_dir("/staging/sonarr")
    monkeypatch.setattr(matching, "match_series", lambda *a, **k: _series_candidate())
    calls = []
    monkeypatch.setattr(sonarr, "import_scan", lambda path: calls.append(path) or True)

    sync.process_sonarr_item("example.test", SONARR_EPISODE)
    sync.on_job_finished(SimpleNamespace(job_id="job-1", status="error"))

    assert calls == []
    # The entry is consumed either way: a manual retry gets a new job id, and
    # nothing should fire again for the one that failed.
    sync.on_job_finished(SimpleNamespace(job_id="job-1", status="done"))
    assert calls == []


def test_a_manual_download_with_no_import_dir_is_never_tracked(
    client, open_panel, stub_jobs, monkeypatch,
):
    """Downloads that never went through _submit_direct_* — a watch, a manual
    request — must not accidentally trigger an import scan."""
    assert sync._pending_imports == {}
    sync.on_job_finished(SimpleNamespace(job_id="job-1", status="done"))
    assert sync._pending_imports == {}


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
