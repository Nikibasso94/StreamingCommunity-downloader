"""Matching a Sonarr/Radarr "wanted" item to a title on the source.

The one rule worth pinning down with tests: a tmdb_id Radarr gives us is
either confirmed exactly or the match is refused outright — a title that
merely sounds right is not an acceptable fallback once an id was on offer.
Sonarr carries no id this source exposes, so its match is title+year
similarity above a high bar, and a year mismatch has to be able to tip an
otherwise-close title below it.
"""

import pytest

from app.integrations import matching


def _candidate(id, name, release_date="2020-01-01", slug="slug"):
    return {"id": id, "name": name, "slug": slug, "release_date": release_date, "poster": None}


@pytest.fixture(autouse=True)
def _domain_version(monkeypatch):
    from app.core import page
    monkeypatch.setattr(page, "get_domain_version", lambda domain: "v1")


def test_match_film_by_exact_tmdb_id(monkeypatch):
    from app.core import metadata, page

    candidates = [_candidate(1, "Film Sbagliato"), _candidate(2, "Film Giusto")]
    monkeypatch.setattr(page, "search", lambda *a, **k: candidates)
    monkeypatch.setattr(
        metadata, "title_metadata",
        lambda media_type, title_id, slug, version: {"tmdb_id": 999 if title_id == 2 else 111},
    )

    result = matching.match_film("Film Qualunque", "2020", 999, "example.test")

    assert result["id"] == 2


def test_match_film_with_a_tmdb_id_never_falls_back_to_a_fuzzy_guess(monkeypatch):
    from app.core import metadata, page

    # The title is an exact string match, but no candidate carries tmdb_id 999.
    monkeypatch.setattr(page, "search", lambda *a, **k: [_candidate(1, "Film Qualunque")])
    monkeypatch.setattr(metadata, "title_metadata", lambda *a, **k: {"tmdb_id": 111})

    assert matching.match_film("Film Qualunque", "2020", 999, "example.test") is None


def test_match_film_without_a_tmdb_id_falls_back_to_title_and_year(monkeypatch):
    from app.core import page

    monkeypatch.setattr(page, "search", lambda *a, **k: [
        _candidate(1, "Tutto Un Altro Film", "1999-01-01"),
        _candidate(2, "Esattamente Questo Titolo", "2020-01-01"),
    ])

    result = matching.match_film("Esattamente Questo Titolo", "2020", None, "example.test")

    assert result["id"] == 2


def test_match_series_rejects_a_dissimilar_title(monkeypatch):
    from app.core import page

    monkeypatch.setattr(page, "search", lambda *a, **k: [_candidate(1, "Qualcosa Di Completamente Diverso")])

    assert matching.match_series("Il Mio Titolo Preciso", "2020", "example.test") is None


def test_match_series_accepts_an_identical_title_with_matching_year(monkeypatch):
    from app.core import page

    monkeypatch.setattr(page, "search", lambda *a, **k: [_candidate(1, "Il Mio Titolo Preciso", "2020-05-01")])

    result = matching.match_series("Il Mio Titolo Preciso", "2020", "example.test")

    assert result["id"] == 1


def test_match_series_year_mismatch_can_tip_a_near_miss_below_threshold(monkeypatch):
    from app.core import page

    # One letter off, and the wrong year: close enough to pass on title alone,
    # but the penalty must be enough to refuse it.
    monkeypatch.setattr(page, "search", lambda *a, **k: [_candidate(1, "Il Mio Titolo Precisoo", "1999-05-01")])

    assert matching.match_series("Il Mio Titolo Preciso", "2020", "example.test") is None


def test_no_search_results_is_no_match(monkeypatch):
    from app.core import page

    monkeypatch.setattr(page, "search", lambda *a, **k: [])

    assert matching.match_film("Qualcosa", "2020", 1, "example.test") is None
    assert matching.match_series("Qualcosa", "2020", "example.test") is None
