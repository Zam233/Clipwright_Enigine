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
        # B21: transform 规范（post_process_timeline 应用）
        self._transform = config.get("transform", {}) or {}

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
        """B21: transform 实现 — 按 YAML transform 规范调整时间线。

        支持的 transform 键：
        - fps: int              覆盖帧率
        - width/height: int     覆盖分辨率
        - add_title_caption: str 在首帧前插入标题字幕
        - duration_cap_sec: float 截断超过该时长的字幕/片段
        """
        if not timeline:
            return timeline

        if self._transform.get("fps"):
            try:
                timeline.fps = int(self._transform["fps"])
            except (TypeError, ValueError):
                pass
        if self._transform.get("width"):
            try:
                timeline.width = int(self._transform["width"])
            except (TypeError, ValueError):
                pass
        if self._transform.get("height"):
            try:
                timeline.height = int(self._transform["height"])
            except (TypeError, ValueError):
                pass

        cap = self._transform.get("duration_cap_sec")
        if isinstance(cap, (int, float)) and cap > 0:
            for track in timeline.tracks or []:
                for clip in track.clips or []:
                    if clip.duration_sec and clip.start_sec + clip.duration_sec > cap:
                        clip.duration_sec = max(0.1, cap - clip.start_sec)

        title = self._transform.get("add_title_caption")
        if title:
            from clipwright.schema.timeline import Clip, ClipKind, Track
            cap_track = next((t for t in (timeline.tracks or []) if t.kind in (ClipKind.CAPTION, ClipKind.TEXT)), None)
            if cap_track is None:
                cap_track = Track(id="title_track", name="标题", kind=ClipKind.CAPTION,
                                  index=len(timeline.tracks or []), clips=[])
                timeline.tracks.append(cap_track)
            cap_track.clips.append(Clip(
                id=f"title_{len(cap_track.clips)}", kind=ClipKind.CAPTION, asset_id="",
                track_id=cap_track.id, start_sec=0, duration_sec=2.0,
                source_offset_sec=0, speed=1, volume=1, opacity=1,
                text=str(title), font_size=56, keyframes=[],
            ))

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
