"""文字动画插件能力 API。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/text-animation", tags=["text-animation"])


@router.get("/capabilities")
async def text_animation_capabilities() -> dict:
    from clipwright.animation.text_engine import TextAnimationEngine
    return TextAnimationEngine().capabilities()


@router.get("/style-packs")
async def text_animation_style_packs() -> dict:
    from clipwright.animation.text_plugin_registry import TextStylePackRegistry
    return {"style_packs": [
        {"id": x["id"], "name": x.get("name") or x["id"], "style": x.get("style") or {}}
        for x in TextStylePackRegistry.list()
    ]}


@router.get("/asset-packs")
async def text_animation_asset_packs() -> dict:
    from clipwright.animation.text_plugin_registry import TextAssetPackRegistry
    return {"asset_packs": [
        {"id": x["id"], "name": x.get("name") or x["id"], "assets": x.get("assets") or []}
        for x in TextAssetPackRegistry.list()
    ]}
