"""Settings and connection tests for the Plex/Sonarr/Radarr connectors.

Open mode grants MANAGE_SETTINGS to every anonymous visitor (the same reason
tests/test_download_hooks.py exercises the panel this way), which is enough to
reach every route under test here without building a session.
"""

import pytest

from app.integrations import plex, radarr, sonarr
from tests.conftest import enable_open_mode


@pytest.fixture(autouse=True)
def _open(client):
    enable_open_mode()


# ── Plex ───────────────────────────────────────────────────────────────────────

def test_plex_defaults_to_unconfigured(client):
    body = client.get("/api/integrations/plex").json()
    assert body == {
        "url": "", "token_masked": "", "connected": False, "refresh_on_download": False,
    }


def test_saving_plex_settings_masks_the_token_back(client):
    response = client.put("/api/integrations/plex", json={
        "url": "http://plex.local:32400", "token": "supersecret", "refresh_on_download": True,
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "http://plex.local:32400"
    assert body["token_masked"] == "…cret"
    assert body["connected"] is True
    assert body["refresh_on_download"] is True
    # The real token is never sent back.
    assert "supersecret" not in response.text


def test_a_blank_token_on_save_keeps_the_stored_one(client):
    client.put("/api/integrations/plex", json={"url": "http://plex.local", "token": "keep-me"})

    client.put("/api/integrations/plex", json={
        "url": "http://plex.local", "token": "", "refresh_on_download": True,
    })

    assert plex.get_config() == ("http://plex.local", "keep-me")


def test_plex_test_connection_reports_missing_config(client):
    response = client.post("/api/integrations/plex/test")
    assert response.json() == {"ok": False, "detail": "URL o token non configurati"}


def test_plex_test_connection_reports_unreachable_server(client, monkeypatch):
    import requests

    client.put("/api/integrations/plex", json={"url": "http://plex.invalid", "token": "x"})

    def fail(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(plex.requests, "get", fail)
    response = client.post("/api/integrations/plex/test")

    body = response.json()
    assert body["ok"] is False
    assert "ConnectionError" in body["detail"]


# ── Sonarr / Radarr ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("service,module", [("sonarr", sonarr), ("radarr", radarr)])
def test_saving_arr_settings_masks_the_api_key_back(client, service, module):
    response = client.put(f"/api/integrations/{service}", json={
        "url": f"http://{service}.local:8989", "api_key": "0123456789abcdef",
        "refresh_on_download": True, "sync_wanted": True,
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == f"http://{service}.local:8989"
    assert body["api_key_masked"] == "…cdef"
    assert body["connected"] is True
    assert body["refresh_on_download"] is True
    assert body["sync_wanted"] is True
    assert "0123456789abcdef" not in response.text
    assert module.get_config() == (f"http://{service}.local:8989", "0123456789abcdef")


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
def test_a_blank_api_key_on_save_keeps_the_stored_one(client, service):
    client.put(f"/api/integrations/{service}", json={"url": "http://x.local", "api_key": "keep-me"})

    client.put(f"/api/integrations/{service}", json={
        "url": "http://x.local", "api_key": "", "sync_wanted": True,
    })

    module = {"sonarr": sonarr, "radarr": radarr}[service]
    assert module.get_config() == ("http://x.local", "keep-me")


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
def test_arr_test_connection_reports_missing_config(client, service):
    response = client.post(f"/api/integrations/{service}/test")
    assert response.json() == {"ok": False, "detail": "URL o API key non configurati"}


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
def test_the_import_dir_round_trips(client, service):
    response = client.put(f"/api/integrations/{service}", json={
        "url": "http://x.local", "api_key": "k", "import_dir": "/data/import",
    })
    assert response.json()["import_dir"] == "/data/import"
    assert client.get(f"/api/integrations/{service}").json()["import_dir"] == "/data/import"


def test_sync_review_is_empty_with_nothing_recorded(client):
    assert client.get("/api/integrations/sync-review").json() == {"items": []}


def test_sync_review_lists_unplaced_items(client):
    from app.integrations import sync

    sync.record_seen("sonarr", "42", "needs_review", "Un Titolo Qualunque")
    sync.record_seen("radarr", "7", "downloading", "Un Film Già Piazzato")

    items = client.get("/api/integrations/sync-review").json()["items"]

    assert [i["external_key"] for i in items] == ["42"]
    assert items[0]["service"] == "sonarr"
    assert items[0]["status"] == "needs_review"
