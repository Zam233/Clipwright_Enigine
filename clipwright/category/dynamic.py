"""动态加载用户自定义视频类型。

扫描 user_types/ 目录下的 YAML 定义文件，
自动注册为 CategoryPlugin。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.config import logger
from clipwright.schema.persona import ParameterLayer
from clipwright.schema.timeline import Timeline


class DynamicCategoryPlugin(BaseCategoryPlugin):
    """从 YAML 定义动态生成的视频类型插件。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.plugin_id = config.get("id", "unknown")
        self.display_name = config.get("name", self.plugin_id)
        self.description = config.get("description", "")
        self._shot_params = config.get("shot_params", {})
        self._persona_mapping = config.get("persona_mapping", {})

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        """使用 YAML 中定义的映射规则翻译 Persona 参数。"""
        translated: dict[str, Any] = {}
        data = params.model_dump(mode="json") if params else {}
        for key, mapping in self._persona_mapping.items():
            source = mapping.get("source", key)
            if source in data:
                translated[key] = data[source]
        return translated

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return {**self._shot_params, **translated}

    def post_process_timeline(self, timeline: Timeline) -> Timeline:
        return timeline


def register_user_types(user_types_dir: str | Path = "user_types") -> list[str]:
    """扫描目录并注册用户自定义视频类型。

    Returns:
        已注册的 type id 列表。
    """
    base = Path(user_types_dir)
    if not base.exists():
        return []

    registered: list[str] = []
    for yaml_file in sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            if not config or "id" not in config:
                logger.warning("跳过无效类型定义: %s (缺少 id 字段)", yaml_file.name)
                continue
            type_id = config["id"]
            if CategoryRegistry.get(type_id) is not None:
                logger.debug("类型已存在，跳过: %s", type_id)
                continue
            plugin = DynamicCategoryPlugin(config)
            CategoryRegistry.register(plugin)
            registered.append(type_id)
            logger.info("注册用户自定义类型: %s (%s)", type_id, yaml_file.name)
        except Exception as e:
            logger.warning("加载用户类型失败 %s: %s", yaml_file.name, e)

    return registered
