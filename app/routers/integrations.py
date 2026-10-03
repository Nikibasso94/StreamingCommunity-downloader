"""Settings and connection tests for the Plex, Sonarr and Radarr connectors.

Shaped after ``app/routers/download_hooks.py``: credentials are masked on the
way out because they are tokens, not opinions, and a blank secret field on
save means "leave the stored one alone" rather than "erase it" — the mask is
what the field shows, so a toggle-only save must not wipe the token under it.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import require
from app.auth.permissions import Permission
from app.config import get_settings, save_settings
from app.integrations import plex, radarr, sonarr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

CAN_MANAGE = [Depends(require(Permission.MANAGE_SETTINGS))]


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


class PlexSettings(BaseModel):
    url: str = ""
    token: str = ""
    refresh_on_download: bool = False


class ArrSettings(BaseModel):
    url: str = ""
    api_key: str = ""
    refresh_on_download: bool = False
    sync_wanted: bool = False
    import_dir: str = ""


def _plex_payload() -> dict:
    url, token = plex.get_config()
    settings = get_settings()
    return {
        "url": url,
        "token_masked": _mask(token),
        "connected": plex.is_connected(),
        "refresh_on_download": bool(settings.get("plex_refresh_on_download")),
    }


def _arr_payload(module, refresh_key: str, sync_key: str) -> dict:
    url, api_key = module.get_config()
    settings = get_settings()
    return {
        "url": url,
        "api_key_masked": _mask(api_key),
        "connected": module.is_connected(),
        "refresh_on_download": bool(settings.get(refresh_key)),
        "sync_wanted": bool(settings.get(sync_key)),
        "import_dir": module.get_import_dir(),
    }


@router.get("/plex", dependencies=CAN_MANAGE)
def get_plex():
    return _plex_payload()


@router.put("/plex", dependencies=CAN_MANAGE)
def put_plex(body: PlexSettings):
    _, current_token = plex.get_config()
    plex.set_config(body.url, body.token or current_token)
    save_settings({**get_settings(), "plex_refresh_on_download": body.refresh_on_download})
    return _plex_payload()


@router.post("/plex/test", dependencies=CAN_MANAGE)
def test_plex():
    ok, detail = plex.test_connection()
    return {"ok": ok, "detail": detail}


@router.get("/sonarr", dependencies=CAN_MANAGE)
def get_sonarr():
    return _arr_payload(sonarr, "sonarr_refresh_on_download", "sonarr_sync_wanted")


@router.put("/sonarr", dependencies=CAN_MANAGE)
def put_sonarr(body: ArrSettings):
    _, current_key = sonarr.get_config()
    sonarr.set_config(body.url, body.api_key or current_key)
    sonarr.set_import_dir(body.import_dir)
    save_settings({
        **get_settings(),
        "sonarr_refresh_on_download": body.refresh_on_download,
        "sonarr_sync_wanted": body.sync_wanted,
    })
    return get_sonarr()


@router.post("/sonarr/test", dependencies=CAN_MANAGE)
def test_sonarr():
    ok, detail = sonarr.test_connection()
    return {"ok": ok, "detail": detail}


@router.get("/radarr", dependencies=CAN_MANAGE)
def get_radarr():
    return _arr_payload(radarr, "radarr_refresh_on_download", "radarr_sync_wanted")


@router.put("/radarr", dependencies=CAN_MANAGE)
def put_radarr(body: ArrSettings):
    _, current_key = radarr.get_config()
    radarr.set_config(body.url, body.api_key or current_key)
    radarr.set_import_dir(body.import_dir)
    save_settings({
        **get_settings(),
        "radarr_refresh_on_download": body.refresh_on_download,
        "radarr_sync_wanted": body.sync_wanted,
    })
    return get_radarr()


@router.post("/radarr/test", dependencies=CAN_MANAGE)
def test_radarr():
    ok, detail = radarr.test_connection()
    return {"ok": ok, "detail": detail}


@router.get("/sync-review", dependencies=CAN_MANAGE)
def sync_review():
    """Wanted items the sync could not place on its own — see app.integrations.sync."""
    from app.integrations import sync as arr_sync

    return {"items": arr_sync.review_items()}
