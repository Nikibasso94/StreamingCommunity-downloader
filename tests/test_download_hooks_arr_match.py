"""The generic post-download refresh (Impostazioni → Integrazioni) matches a
finished job against Radarr/Sonarr's own missing list, not only jobs the
sync itself submitted — a manually downloaded title that happens to be on
either's missing list gets the same move-into-library-and-import handoff a
synced one would.
"""

from types import SimpleNamespace

import pytest

from app import downloads_hooks
from app.config import get_settings, save_settings
from app.integrations import radarr, sonarr


def _film_job(**overrides):
    base = dict(
        job_id="job-1", title="Un Film (2020)", status="done", type="film",
        output_path="/videos/Un Film (2020)/Un Film (2020).mkv",
        season=None, episode_number=None, year="2020", error=None,
        media_label="Un Film", tmdb_id=12345,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _episode_job(**overrides):
    base = dict(
        job_id="job-1", title="Una Serie S01E04", status="done", type="episode",
        output_path="/videos/Una Serie/Season 01/Una Serie S01E04.mkv",
        season=1, episode_number="4", year="2019", error=None,
        media_label="Una Serie", tmdb_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _enabled():
    save_settings({
        **get_settings(),
        "radarr_refresh_on_download": True,
        "sonarr_refresh_on_download": True,
    })


# ── Radarr ─────────────────────────────────────────────────────────────────────

def test_radarr_imports_a_manual_film_that_matches_its_missing_list(monkeypatch):
    movie = {"id": 7, "path": "/radarr-movies/Un Film (2020)"}
    monkeypatch.setattr(radarr, "find_missing_movie", lambda tmdb_id: movie if tmdb_id == 12345 else None)
    calls = []
    monkeypatch.setattr(radarr, "import_into_library", lambda path, m: calls.append((path, m)) or True)
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: pytest.fail("should not do a plain rescan"))

    downloads_hooks._maybe_refresh_radarr(_film_job())

    assert calls == [("/videos/Un Film (2020)/Un Film (2020).mkv", movie)]


def test_radarr_falls_back_to_a_plain_rescan_without_a_match(monkeypatch):
    monkeypatch.setattr(radarr, "find_missing_movie", lambda tmdb_id: None)
    calls = []
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_radarr(_film_job())

    assert calls == ["rescan"]


def test_radarr_falls_back_to_a_plain_rescan_when_the_move_fails(monkeypatch):
    monkeypatch.setattr(radarr, "find_missing_movie", lambda tmdb_id: {"id": 7, "path": "/x"})
    monkeypatch.setattr(radarr, "import_into_library", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_radarr(_film_job())

    assert calls == ["rescan"]


def test_radarr_refresh_is_a_noop_when_the_toggle_is_off(monkeypatch):
    save_settings({**get_settings(), "radarr_refresh_on_download": False})
    calls = []
    monkeypatch.setattr(radarr, "find_missing_movie", lambda *a, **k: calls.append("lookup") or None)
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_radarr(_film_job())

    assert calls == []


def test_radarr_skips_the_lookup_with_no_tmdb_id(monkeypatch):
    """Nothing to confirm an id-match against: straight to the plain rescan,
    never a fuzzy title guess for a film."""
    calls = []
    monkeypatch.setattr(radarr, "find_missing_movie", lambda *a, **k: calls.append("lookup") or None)
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_radarr(_film_job(tmdb_id=None))

    assert calls == ["rescan"]


# ── Sonarr ─────────────────────────────────────────────────────────────────────

def test_sonarr_imports_a_manual_episode_that_matches_its_missing_list(monkeypatch):
    episode = {"id": 9, "series": {"id": 3, "path": "/sonarr-tv/Una Serie"}}
    monkeypatch.setattr(
        sonarr, "find_missing_episode",
        lambda title, season, ep: episode if (title, season, str(ep)) == ("Una Serie", 1, "4") else None,
    )
    calls = []
    monkeypatch.setattr(sonarr, "import_into_library", lambda path, e: calls.append((path, e)) or True)
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: pytest.fail("should not do a plain rescan"))

    downloads_hooks._maybe_refresh_sonarr(_episode_job())

    assert calls == [("/videos/Una Serie/Season 01/Una Serie S01E04.mkv", episode)]


def test_sonarr_falls_back_to_a_plain_rescan_when_the_move_fails(monkeypatch):
    monkeypatch.setattr(sonarr, "find_missing_episode", lambda *a, **k: {"id": 9, "series": {"id": 3, "path": "/x"}})
    monkeypatch.setattr(sonarr, "import_into_library", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_sonarr(_episode_job())

    assert calls == ["rescan"]


def test_sonarr_falls_back_to_a_plain_rescan_without_a_match(monkeypatch):
    monkeypatch.setattr(sonarr, "find_missing_episode", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_sonarr(_episode_job())

    assert calls == ["rescan"]


def test_sonarr_refresh_is_a_noop_when_the_toggle_is_off(monkeypatch):
    save_settings({**get_settings(), "sonarr_refresh_on_download": False})
    calls = []
    monkeypatch.setattr(sonarr, "find_missing_episode", lambda *a, **k: calls.append("lookup") or None)
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_sonarr(_episode_job())

    assert calls == []


def test_a_film_job_never_triggers_the_sonarr_episode_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(sonarr, "find_missing_episode", lambda *a, **k: calls.append("lookup") or None)
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: calls.append("rescan") or True)

    downloads_hooks._maybe_refresh_sonarr(_film_job())

    assert calls == ["rescan"]
