"""视频类型制作 API — 自定义视频类型的 CRUD + 预览。

用户可通过此 API 创建、编辑、删除自定义视频类型定义，
定义以 YAML 格式存储在 user_types/ 目录下。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipwright.category.registry import CategoryRegistry
from clipwright.config import logger
from clipwright.security import validate_id

router = APIRouter(prefix="/api/type-maker", tags=["type-maker"])


async def _guard_type_id(type_id: str | None = None) -> None:
    """路由级守卫：type_id 出现在路径中时校验合法性（防路径遍历）。"""
    if type_id is not None:
        validate_id(type_id, "type_id")


router.dependencies = [Depends(_guard_type_id)]

# 用户自定义类型存储目录
_USER_TYPES_DIR = Path("user_types")


# ── 请求/响应模型 ──────────────────────────────


class ShotParams(BaseModel):
    """镜头级剪辑参数。"""
    min_shot_sec: float = Field(default=1.0, description="最短镜头时长(秒)")
    max_shot_sec: float = Field(default=10.0, description="最长镜头时长(秒)")
    transition_type: str = Field(default="cut", description="默认转场: cut/fade/dissolve/wipe")
    transition_duration_sec: float = Field(default=0.5, description="转场时长(秒)")
    cut_on_beat: bool = Field(default=False, description="是否踩点剪辑")


class PersonaMappingItem(BaseModel):
    """Persona 参数映射规则。"""
    source: str = Field(description="Persona 参数路径")
    transform: str = Field(default="direct", description="转换方式: direct/scale/lookup")
    scale_factor: float = Field(default=1.0, description="缩放因子(scale模式)")


class TypeDefinition(BaseModel):
    """视频类型定义。"""
    id: str = Field(description="类型唯一标识 (英文, 如 my_knowledge)")
    name: str = Field(description="显示名称")
    description: str = Field(default="", description="类型描述")
    shot_params: ShotParams = Field(default_factory=ShotParams)
    persona_mapping: dict[str, PersonaMappingItem] = Field(default_factory=dict)
    post_process: dict[str, Any] = Field(default_factory=dict, description="后处理配置")
    tags: list[str] = Field(default_factory=list)


class TypeListItem(BaseModel):
    """类型列表项。"""
    id: str
    name: str
    description: str
    builtin: bool
    tags: list[str] = Field(default_factory=list)


# ── API 端点 ───────────────────────────────────


@router.get("/list", response_model=list[TypeListItem])
async def list_types() -> list[TypeListItem]:
    """列出所有视频类型（内置 + 自定义）。"""
    result: list[TypeListItem] = []

    # 内置类型
    builtin_ids = {"knowledge_longform", "kichiku_fastcut", "digital_review", "vlog_daily"}
    for item in CategoryRegistry.list():
        result.append(TypeListItem(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            builtin=item["id"] in builtin_ids,
        ))

    # 自定义类型（从 YAML 文件读取 tags）
    if _USER_TYPES_DIR.exists():
        for f in sorted(_USER_TYPES_DIR.glob("*.yaml")) + sorted(_USER_TYPES_DIR.glob("*.yml")):
            try:
                config = yaml.safe_load(f.read_text(encoding="utf-8"))
                if config and config.get("id") and config["id"] not in builtin_ids:
                    if not any(r.id == config["id"] for r in result):
                        result.append(TypeListItem(
                            id=config["id"],
                            name=config.get("name", config["id"]),
                            description=config.get("description", ""),
                            builtin=False,
                            tags=config.get("tags", []),
                        ))
            except Exception:
                continue

    return result


@router.get("/{type_id}")
async def get_type(type_id: str) -> dict:
    """获取视频类型详细定义。"""
    plugin = CategoryRegistry.get(type_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Type '{type_id}' not found")

    # 尝试读取 YAML 源文件
    yaml_path = _find_yaml(type_id)
    if yaml_path:
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        config["_source_file"] = str(yaml_path)
        return config

    # 内置类型返回基本信息
    return {
        "id": plugin.plugin_id,
        "name": plugin.display_name,
        "description": plugin.description,
        "builtin": True,
    }


@router.post("/create", response_model=dict)
async def create_type(definition: TypeDefinition) -> dict:
    """创建自定义视频类型。"""
    validate_id(definition.id, "type_id")
    _USER_TYPES_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = _USER_TYPES_DIR / f"{definition.id}.yaml"

    if yaml_path.exists():
        raise HTTPException(status_code=409, detail=f"Type '{definition.id}' already exists")

    if CategoryRegistry.get(definition.id) is not None:
        raise HTTPException(status_code=409, detail=f"Type '{definition.id}' conflicts with existing type")

    data = definition.model_dump(mode="json")
    yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # 热注册到 CategoryRegistry
    _hot_register(yaml_path)

    logger.info("创建自定义视频类型: %s", definition.id)
    return {"status": "created", "id": definition.id, "file": str(yaml_path)}


@router.put("/{type_id}", response_model=dict)
async def update_type(type_id: str, definition: TypeDefinition) -> dict:
    """更新自定义视频类型。"""
    yaml_path = _find_yaml(type_id)
    if yaml_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Custom type '{type_id}' not found (builtin types cannot be modified)",
        )

    if definition.id != type_id:
        raise HTTPException(status_code=400, detail="Cannot change type ID after creation")

    data = definition.model_dump(mode="json")
    yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # 热更新注册表
    CategoryRegistry.unregister(type_id)
    _hot_register(yaml_path)

    logger.info("更新自定义视频类型: %s", type_id)
    return {"status": "updated", "id": type_id}


@router.delete("/{type_id}")
async def delete_type(type_id: str) -> dict:
    """删除自定义视频类型。"""
    yaml_path = _find_yaml(type_id)
    if yaml_path is None:
        raise HTTPException(status_code=404, detail=f"Custom type '{type_id}' not found")

    CategoryRegistry.unregister(type_id)
    yaml_path.unlink()
    logger.info("删除自定义视频类型: %s", type_id)
    return {"status": "deleted", "id": type_id}


@router.post("/preview")
async def preview_type(definition: TypeDefinition) -> dict:
    """预览类型定义 — 验证配置合法性并返回翻译示例。"""
    errors: list[str] = []

    if not definition.id or not definition.id.replace("_", "").replace("-", "").isalnum():
        errors.append("id 只能包含字母、数字、下划线和连字符")

    sp = definition.shot_params
    if sp.min_shot_sec <= 0:
        errors.append("min_shot_sec 必须大于 0")
    if sp.max_shot_sec < sp.min_shot_sec:
        errors.append("max_shot_sec 不能小于 min_shot_sec")
    if sp.transition_type not in ("cut", "fade", "dissolve", "wipe", "glitch"):
        errors.append(f"不支持的转场类型: {sp.transition_type}")

    # 模拟翻译
    sample_persona = {
        "rhythm": {"cut_density_tier": "high"},
        "visual": {"animation_style": "smooth_fade"},
    }
    translated: dict[str, Any] = {}
    for key, mapping in definition.persona_mapping.items():
        parts = mapping.source.split(".")
        val: Any = sample_persona
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        if val is not None:
            translated[key] = val

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "shot_params": sp.model_dump(),
        "sample_translation": translated,
    }


# ── 辅助函数 ───────────────────────────────────


def _find_yaml(type_id: str) -> Optional[Path]:
    """查找类型对应的 YAML 文件。"""
    if not _USER_TYPES_DIR.exists():
        return None
    for ext in (".yaml", ".yml"):
        p = _USER_TYPES_DIR / f"{type_id}{ext}"
        if p.exists():
            return p
    return None


def _hot_register(yaml_path: Path) -> None:
    """热注册单个 YAML 类型到 CategoryRegistry。"""
    try:
        from clipwright.category.dynamic import DynamicCategoryPlugin
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if config and "id" in config:
            if CategoryRegistry.get(config["id"]) is None:
                CategoryRegistry.register(DynamicCategoryPlugin(config))
    except Exception as e:
        logger.warning("热注册类型失败 %s: %s", yaml_path.name, e)
