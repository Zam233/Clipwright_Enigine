"""代理 API — 生成/切换代理文件。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from clipwright.services.proxy import ProxyGenerator

router = APIRouter(prefix="/api/proxy", tags=["proxy"])


@router.post("/generate")
async def generate_proxy(
    input_path: str = Body(...),
    proxy_height: int = Body(default=720),
) -> dict:
    """为高分辨率素材生成低分辨率代理文件。"""
    result = await ProxyGenerator.generate(input_path, proxy_height)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/switch")
async def switch_to_proxy(
    timeline: dict = Body(...),
    proxy_suffix: str = Body(default="_proxy_720p"),
) -> dict:
    """将 Timeline 中的素材路径替换为代理路径（proxy_suffix='' 时还原原片，C7）。"""
    if proxy_suffix and proxy_suffix.strip():
        return ProxyGenerator.switch_to_proxy(timeline, proxy_suffix)
    return ProxyGenerator.switch_to_full(timeline)
