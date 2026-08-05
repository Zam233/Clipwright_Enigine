"""动态类型插件 — 从 JSON/YAML 配置加载，无需写代码。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from clipwright.schema.persona import ParameterLayer
from clipwright.category.base import BaseCategoryPlugin
from clipwright.config import logger


# 用户自定义类型存储目录
USER_TYPES_DIR = Path("user_types")
USER_TYPES_DIR.mkdir(parents=True, exist_ok=True)


class TypeConfig(BaseModel):
    """视频类型配置的数据模型。"""
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""
    shot_params: dict[str, dict[str, float]] = {
        "low": {"base_shot_ms": 8000, "min_shot_ms": 2000, "max_shot_ms": 15000},
        "medium": {"base_shot_ms": 5000, "min_shot_ms": 1500, "max_shot_ms": 10000},
        "high": {"base_shot_ms": 3000, "min_shot_ms": 800, "max_shot_ms": 8000},
    }
    cut_profile: str = "even_flow"
    transition_weights: dict[str, float] = {"hard_cut": 0.8, "dissolve": 0.1, "fade": 0.1}
    annotation_templates: list[str] = []
    animation_density: str = "medium"
    audio_bgm_slots: dict[str, list[str]] = {}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> TypeConfig:
        return cls(**d)

    def save(self) -> None:
        """保存到 user_types 目录。"""
        path = USER_TYPES_DIR / f"{self.plugin_id}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, plugin_id: str) -> TypeConfig | None:
        path = USER_TYPES_DIR / f"{plugin_id}.json"
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def list_all(cls) -> list[TypeConfig]:
        if not USER_TYPES_DIR.exists():
            return []
        configs = []
        for f in sorted(USER_TYPES_DIR.iterdir()):
            if f.suffix == ".json":
                try:
                    configs.append(cls.from_dict(json.loads(f.read_text(encoding="utf-8"))))
                except Exception as e:
                    logger.warning("加载类型配置失败 %s: %s", f.name, e)
        return configs

    @classmethod
    def delete(cls, plugin_id: str) -> bool:
        path = USER_TYPES_DIR / f"{plugin_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


class DynamicCategoryPlugin(BaseCategoryPlugin):
    """从 TypeConfig 动态加载的视频类型插件。"""

    def __init__(self, config: TypeConfig):
        self._config = config
        self.plugin_id = config.plugin_id
        self.display_name = config.display_name
        self.description = config.description

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        density_map = self._config.shot_params or {}
        tier = getattr(params.rhythm, "cut_density_tier", "medium") if params.rhythm else "medium"
        shot_params = density_map.get(tier, density_map.get("medium", density_map.get(list(density_map.keys())[0] if density_map else {}, {})))

        return {
            "shot_params": shot_params,
            "cut_profile": self._config.cut_profile,
            "transition_weights": self._config.transition_weights,
            "annotation_templates": self._config.annotation_templates,
        }

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return translated.get("shot_params", {})


def register_user_types() -> list[str]:
    """注册所有用户自定义类型到 CategoryRegistry。"""
    from clipwright.category import CategoryRegistry
    configs = TypeConfig.list_all()
    ids = []
    for cfg in configs:
        plugin = DynamicCategoryPlugin(cfg)
        CategoryRegistry.register(plugin)
        ids.append(cfg.plugin_id)
    if ids:
        logger.info("注册 %d 个用户自定义类型: %s", len(ids), ids)
    return ids
