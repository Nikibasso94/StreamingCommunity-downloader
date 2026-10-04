"""Looking up a specific Radarr/Sonarr item by tmdb_id or by title+season+
episode — the half of the manual-download handoff that talks to the real
API, as opposed to app.downloads_hooks' branching logic (see
tests/test_download_hooks_arr_match.py).
"""

from app.integrations import radarr, sonarr


def test_find_missing_movie_matches_by_exact_tmdb_id(client, monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": True, "hasFile": False},
        {"id": 2, "tmdbId": 222, "monitored": True, "hasFile": False},
    ])

    movie = radarr.find_missing_movie(222)

    assert movie["id"] == 2


def test_find_missing_movie_ignores_one_that_already_has_a_file(client, monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": True, "hasFile": True},
    ])

    assert radarr.find_missing_movie(111) is None


def test_find_missing_movie_ignores_an_unmonitored_one(client, monkeypatch):
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


def test_find_missing_episode_matches_series_by_title_then_episode(client, monkeypatch):
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
    assert episode["series"]["id"] == 10  # needed to place the file and to scope the rescan


def test_find_missing_episode_requires_a_close_series_title(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))
    monkeypatch.setattr(sonarr.arr_client, "get", lambda *a, **k: [{"id": 10, "title": "Qualcosa Di Diverso"}])

    assert sonarr.find_missing_episode("Una Serie Qualunque", 1, "4") is None


def test_find_missing_episode_ignores_one_that_already_has_a_file(client, monkeypatch):
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


# ── Placing the file and importing ──────────────────────────────────────────
#
# arr_client.place_in_library does the actual filesystem move; verified
# against a real Sonarr/Radarr that a file simply sitting in the right
# folder, under any name, is enough for RescanMovie/RescanSeries to pick it
# up — see the commit message for how that was checked, and why the earlier
# DownloadedMoviesScan/DownloadedEpisodesScan approach did not work.

def test_place_in_library_moves_the_file_and_its_sidecars(tmp_path):
    from app.integrations import arr_client

    source_dir = tmp_path / "downloads"
    source_dir.mkdir()
    video = source_dir / "Film.mkv"
    video.write_text("video")
    subtitle = source_dir / "Film.it.vtt"
    subtitle.write_text("subs")
    unrelated = source_dir / "Other Film.mkv"
    unrelated.write_text("other")

    target_dir = tmp_path / "library" / "Film (2020)"

    new_path = arr_client.place_in_library(str(video), str(target_dir))

    assert new_path == str(target_dir / "Film.mkv")
    assert (target_dir / "Film.mkv").read_text() == "video"
    assert (target_dir / "Film.it.vtt").read_text() == "subs"
    assert not video.exists()
    assert unrelated.exists()  # a different stem, left alone
    assert source_dir.exists()  # not empty (unrelated is still there), so not removed


def test_place_in_library_removes_the_source_folder_once_it_is_empty(tmp_path):
    from app.integrations import arr_client

    source_dir = tmp_path / "downloads" / "Film (2020)"
    source_dir.mkdir(parents=True)
    video = source_dir / "Film.mkv"
    video.write_text("video")

    arr_client.place_in_library(str(video), str(tmp_path / "library" / "Film (2020)"))

    assert not source_dir.exists()


def test_place_in_library_leaves_a_non_empty_source_folder_alone(tmp_path):
    """A poster, an .nfo, anything some other tool left there must not be
    silently discarded just because the video moved out."""
    from app.integrations import arr_client

    source_dir = tmp_path / "downloads" / "Film (2020)"
    source_dir.mkdir(parents=True)
    video = source_dir / "Film.mkv"
    video.write_text("video")
    (source_dir / "poster.jpg").write_text("poster")

    arr_client.place_in_library(str(video), str(tmp_path / "library" / "Film (2020)"))

    assert source_dir.exists()
    assert (source_dir / "poster.jpg").exists()


def test_place_in_library_returns_none_on_failure(tmp_path, monkeypatch):
    from app.integrations import arr_client

    monkeypatch.setattr(arr_client.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    assert arr_client.place_in_library(str(tmp_path / "Film.mkv"), str(tmp_path / "target")) is None


def test_radarr_import_into_library_moves_then_scopes_the_rescan(tmp_path, monkeypatch):
    video = tmp_path / "Film.mkv"
    video.write_text("video")
    target_dir = tmp_path / "library" / "Film (2020)"
    movie = {"id": 7, "path": str(target_dir)}

    calls = []
    monkeypatch.setattr(radarr, "rescan_movie", lambda movie_id: calls.append(movie_id) or True)

    ok = radarr.import_into_library(str(video), movie)

    assert ok is True
    assert calls == [7]
    assert (target_dir / "Film.mkv").exists()


def test_radarr_import_into_library_skips_rescan_when_the_move_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(radarr.arr_client, "place_in_library", lambda *a, **k: None)
    monkeypatch.setattr(radarr, "rescan_movie", lambda *a, **k: calls.append("rescan") or True)

    ok = radarr.import_into_library("/nowhere/Film.mkv", {"id": 7, "path": "/x"})

    assert ok is False
    assert calls == []


def test_sonarr_import_into_library_moves_then_scopes_the_rescan(tmp_path, monkeypatch):
    video = tmp_path / "Episode.mkv"
    video.write_text("video")
    target_dir = tmp_path / "library" / "Una Serie"
    episode = {"id": 9, "series": {"id": 3, "path": str(target_dir)}}

    calls = []
    monkeypatch.setattr(sonarr, "rescan_series", lambda series_id: calls.append(series_id) or True)

    ok = sonarr.import_into_library(str(video), episode)

    assert ok is True
    assert calls == [3]
    assert (target_dir / "Episode.mkv").exists()


def test_sonarr_import_into_library_skips_rescan_when_the_move_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(sonarr.arr_client, "place_in_library", lambda *a, **k: None)
    monkeypatch.setattr(sonarr, "rescan_series", lambda *a, **k: calls.append("rescan") or True)

    ok = sonarr.import_into_library("/nowhere/Episode.mkv", {"id": 9, "series": {"id": 3, "path": "/x"}})

    assert ok is False
    assert calls == []


# ── Skip tag ─────────────────────────────────────────────────────────────────

def test_radarr_wanted_missing_filters_out_the_skip_tag(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr, "get_skip_tag", lambda: "no-panel")
    monkeypatch.setattr(radarr.arr_client, "resolve_tag_id", lambda *a, **k: 5)
    monkeypatch.setattr(radarr.arr_client, "wanted_missing_all", lambda *a, **k: [
        {"id": 1, "tags": [5]},
        {"id": 2, "tags": [1, 2]},
        {"id": 3, "tags": []},
    ])

    movies = radarr.wanted_missing()

    assert [m["id"] for m in movies] == [2, 3]


def test_radarr_wanted_missing_is_unfiltered_without_a_skip_tag(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr, "get_skip_tag", lambda: "")
    monkeypatch.setattr(radarr.arr_client, "wanted_missing_all", lambda *a, **k: [{"id": 1, "tags": [5]}])

    movies = radarr.wanted_missing()

    assert [m["id"] for m in movies] == [1]


def test_find_missing_movie_skips_a_tagged_one(monkeypatch):
    monkeypatch.setattr(radarr, "get_config", lambda: ("http://radarr.local", "key"))
    monkeypatch.setattr(radarr, "get_skip_tag", lambda: "no-panel")
    monkeypatch.setattr(radarr.arr_client, "resolve_tag_id", lambda *a, **k: 5)
    monkeypatch.setattr(radarr.arr_client, "get", lambda *a, **k: [
        {"id": 1, "tmdbId": 111, "monitored": True, "hasFile": False, "tags": [5]},
    ])

    assert radarr.find_missing_movie(111) is None


def test_sonarr_wanted_missing_filters_out_series_with_the_skip_tag(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))
    monkeypatch.setattr(sonarr, "get_skip_tag", lambda: "no-panel")
    monkeypatch.setattr(sonarr.arr_client, "resolve_tag_id", lambda *a, **k: 9)
    monkeypatch.setattr(sonarr.arr_client, "wanted_missing_all", lambda *a, **k: [
        {"id": 1, "series": {"tags": [9]}},
        {"id": 2, "series": {"tags": []}},
    ])

    episodes = sonarr.wanted_missing()

    assert [e["id"] for e in episodes] == [2]


def test_find_missing_episode_skips_a_tagged_series(monkeypatch):
    monkeypatch.setattr(sonarr, "get_config", lambda: ("http://sonarr.local", "key"))
    monkeypatch.setattr(sonarr, "get_skip_tag", lambda: "no-panel")
    monkeypatch.setattr(sonarr.arr_client, "resolve_tag_id", lambda *a, **k: 9)
    monkeypatch.setattr(sonarr.arr_client, "get", lambda *a, **k: [{"id": 10, "title": "Una Serie", "tags": [9]}])

    assert sonarr.find_missing_episode("Una Serie", 1, "4") is None


def test_resolve_tag_id_matches_case_insensitively(monkeypatch):
    from app.integrations import arr_client

    monkeypatch.setattr(arr_client, "get", lambda *a, **k: [{"id": 7, "label": "No-Panel"}])

    assert arr_client.resolve_tag_id("http://x", "key", "no-panel") == 7


def test_resolve_tag_id_returns_none_when_blank(monkeypatch):
    from app.integrations import arr_client

    assert arr_client.resolve_tag_id("http://x", "key", "") is None
    assert arr_client.resolve_tag_id("http://x", "key", "   ") is None


def test_resolve_tag_id_returns_none_on_network_error(monkeypatch):
    from app.integrations import arr_client

    def fail(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(arr_client, "get", fail)

    assert arr_client.resolve_tag_id("http://x", "key", "no-panel") is None
