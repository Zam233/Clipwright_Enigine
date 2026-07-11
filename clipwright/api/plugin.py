"""插件 API — 第三方插件的发现、加载、管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from clipwright.plugins import PluginLoadError
from clipwright.schema.plugin import PluginMetadata

# PluginLoader 在 main.py lifespan 中注入
_loader: "PluginLoader | None" = None  # noqa: F821


def set_loader(loader: "PluginLoader") -> None:  # noqa: F821
    global _loader
    _loader = loader


router = APIRouter(prefix="/api/plugin", tags=["plugin"])


@router.get("/list", response_model=list[PluginMetadata])
async def list_plugins() -> list[PluginMetadata]:
    """列出所有已加载的第三方插件。"""
    if _loader is None:
        return []
    return _loader.list_loaded()


@router.get("/discover", response_model=list[str])
async def discover_plugins() -> list[str]:
    """发现插件目录中的所有可用插件（不加载）。"""
    if _loader is None:
        return []
    return _loader.discover()


@router.post("/load/{plugin_id}", response_model=PluginMetadata)
async def load_plugin(plugin_id: str) -> PluginMetadata:
    """加载并初始化指定插件。"""
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    try:
        plugin = _loader.load(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
        meta = _loader.list_loaded()
        for m in meta:
            if m.manifest.id == plugin_id:
                return m
        raise HTTPException(status_code=500, detail="Plugin loaded but metadata missing")
    except PluginLoadError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/unload/{plugin_id}")
async def unload_plugin(plugin_id: str) -> dict[str, str]:
    """卸载指定插件。"""
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.unload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.post("/load-all", response_model=list[str])
async def load_all_plugins() -> list[str]:
    """发现并加载所有可用插件。"""
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    return _loader.load_all()


@router.get("/capabilities")
async def get_capabilities() -> dict:
    """获取系统全部能力概览（插件 + 工具 + 技能 + 素材源）。"""
    from clipwright.material import MaterialRegistry
    from clipwright.skill import SkillRegistry
    from clipwright.tool import ToolRegistry

    return {
        "tools": ToolRegistry.list(),
        "skills": SkillRegistry.list(),
        "material_sources": MaterialRegistry.list(),
        "plugins": _loader.list_loaded() if _loader else [],
    }
