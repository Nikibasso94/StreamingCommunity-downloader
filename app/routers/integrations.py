"""Settings and connection tests for the Plex, Sonarr and Radarr connectors.

Shaped after ``app/routers/download_hooks.py``: credentials are masked on the
way out because they are tokens, not opinions, and a blank secret field on
save means "leave the stored one alone" rather than "erase it" — the mask is
what the field shows, so a toggle-only save must not wipe the token under it.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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


@router.post("/sync-now", dependencies=CAN_MANAGE)
async def sync_now():
    """Run the wanted-list sync immediately instead of waiting for the next
    cycle — same shape as the "Controlla ora" button on a followed series."""
    from app.integrations import sync as arr_sync

    try:
        return await asyncio.to_thread(arr_sync.run_sync_cycle)
    except Exception as exc:
        logger.exception("Manual arr sync failed")
        raise HTTPException(status_code=502, detail=f"Sincronizzazione fallita: {exc}")


class ReviewResolve(BaseModel):
    service: str = Field(pattern="^(sonarr|radarr)$")
    external_key: str
    candidate_id: str
    candidate_slug: str | None = None
    candidate_poster: str | None = None


@router.post("/sync-review/resolve", dependencies=CAN_MANAGE)
async def resolve_sync_review(body: ReviewResolve):
    """A human picked the right title for an item the matcher could not place."""
    from app.integrations import sync as arr_sync

    candidate = {
        "id": body.candidate_id, "slug": body.candidate_slug, "poster": body.candidate_poster,
    }
    try:
        outcome = await asyncio.to_thread(
            arr_sync.resolve_review_item, body.service, body.external_key, candidate
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"outcome": outcome}


@router.delete("/sync-review/{service}/{external_key}", dependencies=CAN_MANAGE)
def dismiss_sync_review(service: str, external_key: str):
    from app.integrations import sync as arr_sync

    if service not in ("sonarr", "radarr"):
        raise HTTPException(status_code=404, detail="Servizio sconosciuto")
    arr_sync.dismiss_review_item(service, external_key)
    return {"ok": True}
