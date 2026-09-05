"""插件 API — 第三方插件的发现、加载、管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from clipwright.plugins import PluginLoadError
from clipwright.schema.plugin import PluginMetadata

# PluginLoader 在 main.py lifespan 中注入
_loader: "PluginLoader | None" = None  # noqa: F821


def set_loader(loader: "PluginLoader") -> None:  # noqa: F821
    global _loader
    _loader = loader


router = APIRouter(prefix="/api/plugin", tags=["plugin"])


def _require_admin(request: Request) -> None:
    """P3: jwt 模式下插件写操作需 admin 角色；off/token 模式放行。"""
    from clipwright.authz import current_user_id, is_admin
    uid = current_user_id(request)
    if uid is not None and not is_admin(request):
        raise HTTPException(status_code=403, detail="插件管理操作需要管理员权限")


def _validate_plugin_id(plugin_id: str) -> str:
    """审计 P1 修复：所有含 plugin_id 的端点先校验 ID 合法性，
    防止 ../ 路径穿越（如 GET /{plugin_id}/ui 直接拼接文件路径）。"""
    from clipwright.security import SecurityViolation, validate_id
    try:
        return validate_id(plugin_id, "plugin_id")
    except SecurityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))


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
async def load_plugin(plugin_id: str, request: Request = None) -> PluginMetadata:
    """加载并初始化指定插件。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
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
async def unload_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """卸载指定插件。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.unload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """M8: 启用插件（持久化 + 加载）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.set_enabled(plugin_id, True)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": "true"}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """M8: 禁用插件（持久化 + 卸载）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.set_enabled(plugin_id, False)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": "false"}


@router.get("/permissions")
async def list_permissions() -> dict[str, object]:
    """M1: 已知权限白名单（供前端安装确认展示）。"""
    from clipwright.config import settings
    return {"allowed": settings.plugin_allowed_permissions}


# ── M7: 插件错误通道 ──


@router.get("/errors")
async def list_plugin_errors(limit: int = 50) -> list[dict]:
    """M7: 插件错误通道 — 最近错误列表（诊断用）。"""
    from clipwright.plugins.error_bus import get_error_bus
    return get_error_bus().list(limit=max(1, min(limit, 200)))


@router.delete("/errors")
async def clear_plugin_errors(plugin_id: str = "") -> dict[str, object]:
    """M7: 清空插件错误通道（可按插件过滤）。"""
    from clipwright.plugins.error_bus import get_error_bus
    removed = get_error_bus().clear(plugin_id or None)
    return {"status": "ok", "removed": removed}


@router.delete("/{plugin_id}")
async def unregister_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """P1-1: 注销插件 — 卸载 + 清理 PluginData（含 hook 与配置）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if _loader.get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not loaded")
    _loader.unload(plugin_id)
    # 清理 PluginData 目录（config/数据）
    try:
        data_dir = _loader.get_plugin_data_dir(plugin_id, ensure=False)
        import shutil
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("插件数据目录清理失败 %s: %s", plugin_id, e)
    # P1-1: 注销已注册的 Hook
    try:
        from clipwright.plugins.hooks import HookRegistry
        for point in list(HookRegistry._hooks.keys()):
            HookRegistry._hooks[point] = [
                fn for fn in HookRegistry._hooks[point]
                if getattr(fn, "__plugin_id__", None) != plugin_id
            ]
    except Exception as e:
        from clipwright.config import logger
        logger.warning("插件 Hook 清理失败 %s: %s", plugin_id, e)
    return {"status": "unregistered", "plugin_id": plugin_id}


# ── 插件配置管理 ──

@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, object]:
    """读取插件结构化配置（含 type/value/label 元数据）。

    M13: 已发现但未加载的插件同样返回（源码默认 + PluginData 覆盖），
    支持预配置；完全不存在（未发现）的插件返回 404。
    """
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return _loader.get_typed_config(plugin_id)


@router.put("/{plugin_id}/config")
async def put_plugin_config(plugin_id: str, request: Request) -> dict[str, object]:
    """写入插件配置（YAML 格式），校验类型后存入 PluginData。

    M13: 已发现但未加载的插件同样可保存（预配置），加载后生效。
    """
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    import yaml

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Config must be a YAML mapping")

    # 校验结构化配置
    from clipwright.plugins.config_types import validate_typed_config
    errors = validate_typed_config(data)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "配置校验失败", "errors": errors})

    # P5: secret 掩码回写防护——如果 PUT 值包含 ****（掩码标记），保留原加密值
    from clipwright.plugins.config_types import mask_secret_value
    _plugin = _loader.get(plugin_id)
    if _plugin and hasattr(_plugin, 'config') and isinstance(_plugin.config, dict):
        for fname, fdef in (data.get("fields") or {}).items():
            if isinstance(fdef, dict) and isinstance(fdef.get("value"), str) and "****" in fdef["value"]:
                orig_val = _plugin.config.get(fname)
                if orig_val is not None:
                    fdef["value"] = orig_val  # 保留原始加密值
    _loader.save_config(plugin_id, data)
    _loader.reload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.delete("/{plugin_id}/config")
async def delete_plugin_config(plugin_id: str) -> dict[str, str]:
    """删除数据目录的 config.yaml，回退到源码默认配置。"""
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    config_path = _loader.get_plugin_data_dir(plugin_id) / "config.yaml"

    if config_path.exists():
        config_path.unlink()

    # 刷新并重载
    plugin = _loader.get(plugin_id)
    if plugin:
        from clipwright.plugins.loader import _extract_flat_values
        plugin.config = _extract_flat_values(_loader._get_merged_config(plugin_id))

    _loader.reload(plugin_id)
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


# ── 插件前端 UI ──


@router.get("/{plugin_id}/ui")
async def get_plugin_ui(plugin_id: str) -> dict:
    """返回插件的 UI 布局定义（ui.json）。"""
    _validate_plugin_id(plugin_id)  # 审计 P1 修复：阻断 ../ 路径穿越
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    ui_file = _loader.plugin_dir / plugin_id / "ui.json"
    if not ui_file.exists():
        return {"widgets": []}
    try:
        import json as _json
        return _json.loads(ui_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read plugin UI: {e}")
