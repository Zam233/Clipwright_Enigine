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
from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
)
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
        # transform 规范（post_process_timeline 应用）
        self._transform = config.get("transform", {}) or {}
        # 类型规范（A-07）：YAML 里的 prompts / param_rules / signature_traits / declares
        self._raw_prompts = config.get("prompts", {}) or {}
        self._raw_rules = config.get("param_rules", []) or []
        self._raw_traits = config.get("signature_traits", []) or []
        self._raw_declares = config.get("declares", {}) or {}

    # ── 类型规范（A-07）──────────────────────────────────────────────

    def get_prompt_bundle(self) -> PromptBundle:
        """从 YAML 读提示词；未声明时回落到基类的旧钩子适配器。"""
        if not self._raw_prompts:
            return super().get_prompt_bundle()
        slots: dict[str, PromptSlot] = {}
        for key, raw in self._raw_prompts.items():
            if isinstance(raw, str):
                slots[key] = PromptSlot(text=raw)
            elif isinstance(raw, dict):
                try:
                    slots[key] = PromptSlot(**raw)
                except Exception:
                    logger.warning("类型 %s 槽位 %s 非法，已跳过", self.plugin_id, key)
        return PromptBundle(slots=slots).normalized()

    def get_param_rules(self) -> list[ParamRule]:
        rules: list[ParamRule] = []
        for raw in self._raw_rules:
            if not isinstance(raw, dict):
                continue
            try:
                rules.append(ParamRule(**raw))
            except Exception:
                logger.warning("类型 %s 的参数规则非法，已跳过: %s", self.plugin_id, raw)
        return rules

    def get_signature_traits(self) -> list[SignatureTrait]:
        traits: list[SignatureTrait] = []
        for raw in self._raw_traits:
            if not isinstance(raw, dict):
                continue
            try:
                traits.append(SignatureTrait(**raw))
            except Exception:
                logger.warning("类型 %s 的指标非法，已跳过: %s", self.plugin_id, raw)
        return traits

    def get_declares(self) -> TypeDeclares:
        if not self._raw_declares:
            return super().get_declares()
        try:
            return TypeDeclares(**self._raw_declares)
        except Exception:
            logger.warning("类型 %s 的 declares 非法，回落默认", self.plugin_id)
            return super().get_declares()

    @staticmethod
    def _resolve_path(data: dict[str, Any], path: str) -> Any:
        """P1-15: 按 "." 逐层取嵌套值——旧实现 `"rhythm.cut_density_tier" in data`
        恒为 False（model_dump 是嵌套 dict），映射整体空转。"""
        cur: Any = data
        for part in str(path).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def _apply_transform(self, mapping: dict[str, Any], value: Any) -> Any:
        """P1-15: mapping.transform — direct（默认）/ scale（×scale_factor）/ lookup（查表）。"""
        t = str(mapping.get("transform", "direct") or "direct")
        if value is None:
            return None
        try:
            if t == "scale":
                factor = float(mapping.get("scale_factor", 1.0) or 1.0)
                return value * factor if isinstance(value, (int, float)) else value
            if t == "lookup":
                table = mapping.get("lookup") or {}
                return table.get(str(value), value)
        except (TypeError, ValueError):
            return value
        return value

    def _shot_params_ms(self) -> dict[str, Any]:
        """P1-15: YAML shot_params（秒键）→ EditAgent 消费的 ms 键。

        旧实现把 YAML 原样并入 extra_params.shot_params，而 EditAgent 读的是
        base_shot_ms/min_shot_ms/max_shot_ms → 自定义类型的节奏配置整体失效。
        """
        out: dict[str, Any] = {}
        for k, v in (self._shot_params or {}).items():
            if k.endswith("_sec") and isinstance(v, (int, float)):
                out[f"{k[:-4]}_ms"] = int(round(v * 1000))
            elif k == "base_shot_duration_ms" and isinstance(v, (int, float)):
                out["base_shot_ms"] = int(v)
            elif k == "base_shot_ms":
                out["base_shot_ms"] = v
            else:
                out[k] = v
        return out

    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        """使用 YAML 中定义的映射规则翻译 Persona 参数。

        P1-15: 返回值并入 shot_params（ms 键）与 shot_params 中的开关
        （cut_on_beat / transition_weights）——内置类型均会返回 shot_params，
        动态类型此前只返回映射结果，EditAgent 拿到空 shot_params 回退 5000ms。
        """
        translated: dict[str, Any] = {}
        data = params.model_dump(mode="json") if params else {}
        for key, mapping in self._persona_mapping.items():
            if not isinstance(mapping, dict):
                continue
            source = mapping.get("source", key)
            value = self._resolve_path(data, source)
            value = self._apply_transform(mapping, value)
            if value is not None:
                translated[key] = value

        result: dict[str, Any] = {**translated}
        shot_params = self._shot_params_ms()
        if shot_params:
            result["shot_params"] = shot_params
        # YAML shot_params 里的编排开关提升到 extra_params 顶层（与内置类型对齐）
        sp = self._shot_params or {}
        if sp.get("cut_on_beat") is not None:
            result["cut_on_beat"] = bool(sp["cut_on_beat"])
        if sp.get("transition_type"):
            result["transition_weights"] = {str(sp["transition_type"]): 1.0}
        return result

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
