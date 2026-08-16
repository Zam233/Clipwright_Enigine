"""市场 API（P4-4B）— 浏览 ClipWright Server 市场 + 一键安装到本地。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from clipwright.services import install_service, market_client

router = APIRouter(prefix="/api/market", tags=["market"])


def _market_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"市场服务不可用: {e}")


# ── 插件 ──

@router.get("/plugins")
async def list_market_plugins(q: str = "", tag: str = "", page: int = 1):
    try:
        return await market_client.search_plugins(q, tag, page)
    except httpx.HTTPError as e:
        raise _market_error(e)


@router.get("/plugins/{plugin_id}")
async def market_plugin_detail(plugin_id: str):
    try:
        return await market_client.get_plugin(plugin_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=404 if e.response.status_code == 404 else 502,
                            detail=f"市场插件不存在或服务异常: {e}")
    except httpx.HTTPError as e:
        raise _market_error(e)


@router.post("/plugins/{plugin_id}/install")
async def install_market_plugin(plugin_id: str, version: str = ""):
    try:
        return await install_service.install_plugin(plugin_id, version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        raise _market_error(e)


# ── Persona ──

@router.get("/personas")
async def list_market_personas(q: str = "", tag: str = "", page: int = 1):
    try:
        return await market_client.search_personas(q, tag, page)
    except httpx.HTTPError as e:
        raise _market_error(e)


@router.get("/personas/{persona_id}")
async def market_persona_detail(persona_id: str):
    try:
        return await market_client.get_persona(persona_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=404 if e.response.status_code == 404 else 502,
                            detail=f"市场 Persona 不存在或服务异常: {e}")
    except httpx.HTTPError as e:
        raise _market_error(e)


@router.post("/personas/{persona_id}/install")
async def install_market_persona(persona_id: str, version: str = ""):
    try:
        return await install_service.install_persona(persona_id, version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        raise _market_error(e)
