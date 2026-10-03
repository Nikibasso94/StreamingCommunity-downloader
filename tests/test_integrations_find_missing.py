"""Looking up a specific Radarr/Sonarr item by tmdb_id or by title+season+
episode — the half of the manual-download handoff that talks to the real
API, as opposed to app.downloads_hooks' branching logic (see
tests/test_download_hooks_arr_match.py).
"""

from app.integrations import radarr, sonarr


def test_find_missing_movie_matches_by_exact_tmdb_id(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": True, "hasFile": False},
        {"id": 2, "tmdbId": 222, "monitored": True, "hasFile": False},
    ])

    movie = radarr.find_missing_movie(222)

    assert movie["id"] == 2


def test_find_missing_movie_ignores_one_that_already_has_a_file(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": True, "hasFile": True},
    ])

    assert radarr.find_missing_movie(111) is None


def test_find_missing_movie_ignores_an_unmonitored_one(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": False, "hasFile": False},
    ])

    assert radarr.find_missing_movie(111) is None


def test_find_missing_movie_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("", ""))

    assert radarr.find_missing_movie(111) is None


def test_find_missing_movie_returns_none_on_a_network_error(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))

    def fail(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(radarr.arr_client, "get", fail)

    assert radarr.find_missing_movie(111) is None


def test_find_missing_episode_matches_series_by_title_then_episode(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))

    def fake_get(url, api_key, path, params=None):
        if path == "series":
            return [{"id": 10, "title": "Una Serie Qualunque"}]
        if path == "episode":
            assert params == {"seriesId": 10}
            return [
                {"seasonNumber": 1, "episodeNumber": 3, "monitored": True, "hasFile": False},
                {"seasonNumber": 1, "episodeNumber": 4, "monitored": True, "hasFile": False},
            ]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(sonarr.arr_client, "get", fake_get)

    episode = sonarr.find_missing_episode("Una Serie Qualunque", 1, "4")

    assert episode["episodeNumber"] == 4


def test_find_missing_episode_requires_a_close_series_title(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))
    monkeypatch.setattr(sonarr.arr_client, "get", lambda *a, **k: [{"id": 10, "title": "Qualcosa Di Diverso"}])

    assert sonarr.find_missing_episode("Una Serie Qualunque", 1, "4") is None


def test_find_missing_episode_ignores_one_that_already_has_a_file(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))

    def fake_get(url, api_key, path, params=None):
        if path == "series":
            return [{"id": 10, "title": "Una Serie Qualunque"}]
        return [{"seasonNumber": 1, "episodeNumber": 4, "monitored": True, "hasFile": True}]

    monkeypatch.setattr(sonarr.arr_client, "get", fake_get)

    assert sonarr.find_missing_episode("Una Serie Qualunque", 1, "4") is None


def test_find_missing_episode_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("", ""))

    assert sonarr.find_missing_episode("Una Serie", 1, "4") is None
