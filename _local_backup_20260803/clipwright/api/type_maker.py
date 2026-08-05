"""类型制作器 API — 创建/编辑/管理用户自定义视频类型。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.category.dynamic import TypeConfig, DynamicCategoryPlugin, register_user_types
from clipwright.category import CategoryRegistry

router = APIRouter(prefix="/api/type-maker", tags=["type-maker"])


@router.post("/create")
async def create_type(config: TypeConfig) -> dict:
    """创建新的视频类型。"""
    # 检查是否已存在
    existing = TypeConfig.load(config.plugin_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"类型 '{config.plugin_id}' 已存在")
    config.save()
    # 注册到运行时
    plugin = DynamicCategoryPlugin(config)
    CategoryRegistry.register(plugin)
    logger.info("类型已创建: %s (%s)", config.plugin_id, config.display_name)
    return {"status": "created", "plugin_id": config.plugin_id}


@router.put("/update/{plugin_id}")
async def update_type(plugin_id: str, config: dict[str, Any]) -> dict:
    """更新已有视频类型的配置。"""
    existing = TypeConfig.load(plugin_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"类型 '{plugin_id}' 不存在")
    cfg = TypeConfig.from_dict({**existing.to_dict(), **config, "plugin_id": plugin_id})
    cfg.save()
    # 重新注册
    CategoryRegistry._plugins = {k: v for k, v in CategoryRegistry._plugins.items() if v.plugin_id != plugin_id}
    plugin = DynamicCategoryPlugin(cfg)
    CategoryRegistry.register(plugin)
    logger.info("类型已更新: %s", plugin_id)
    return {"status": "updated", "plugin_id": plugin_id}


@router.get("/list")
async def list_types() -> list[dict[str, Any]]:
    """列出所有用户自定义类型。"""
    configs = TypeConfig.list_all()
    return [c.to_dict() for c in configs]


@router.get("/get/{plugin_id}")
async def get_type(plugin_id: str) -> dict:
    """获取单个类型配置。"""
    cfg = TypeConfig.load(plugin_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"类型 '{plugin_id}' 不存在")
    return cfg.to_dict()


@router.delete("/delete/{plugin_id}")
async def delete_type(plugin_id: str) -> dict:
    """删除用户自定义类型。"""
    ok = TypeConfig.delete(plugin_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"类型 '{plugin_id}' 不存在")
    # 从运行中移除
    CategoryRegistry._plugins = {k: v for k, v in CategoryRegistry._plugins.items() if v.plugin_id != plugin_id}
    logger.info("类型已删除: %s", plugin_id)
    return {"status": "deleted", "plugin_id": plugin_id}


@router.post("/duplicate/{plugin_id}")
async def duplicate_type(plugin_id: str, new_id: str = "", new_name: str = "") -> dict:
    """复制一个已有类型作为新类型。"""
    cfg = TypeConfig.load(plugin_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"源类型 '{plugin_id}' 不存在")
    target_id = new_id or f"{plugin_id}_copy"
    # 检查目标是否已存在
    if TypeConfig.load(target_id):
        raise HTTPException(status_code=409, detail=f"目标类型 '{target_id}' 已存在")
    new_cfg = TypeConfig(
        plugin_id=target_id,
        display_name=new_name or f"{cfg.display_name} 副本",
        description=cfg.description,
        shot_params=cfg.shot_params,
        cut_profile=cfg.cut_profile,
        transition_weights=cfg.transition_weights,
        annotation_templates=cfg.annotation_templates,
        animation_density=cfg.animation_density,
    )
    new_cfg.save()
    plugin = DynamicCategoryPlugin(new_cfg)
    CategoryRegistry.register(plugin)
    return {"status": "duplicated", "plugin_id": target_id}
