"""MG 动画插件 — 自定义运动图形动画，JSON 格式定义，自动注册到 AnimationRegistry。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.plugins.base import BasePlugin
from clipwright.plugins.hooks import HookRegistry, HookPoint
from clipwright.schema.animation import (
    AnimationDef,
    AnimationTarget,
    AnimationType,
    EasingFunction,
    Keyframe,
    PropertyDef,
)

# MG 动画也可通过 DIAGRAM_RENDERER_EXTEND hook 注册为逻辑动画类型
# 让结构 Agent 能在 prompt 中列出它们


def _load_mg_animations(
    animations_dir: Path,
) -> list[dict[str, Any]]:
    """从 animations/ 目录加载所有 JSON 动画定义。"""
    if not animations_dir.exists():
        logger.warning("MG 动画目录不存在: %s", animations_dir)
        return []

    mg_anims = []
    for f in sorted(animations_dir.iterdir()):
        if f.suffix == ".json":
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                if "animation_id" in data and "name" in data:
                    mg_anims.append(data)
                    logger.info("加载 MG 动画: %s (%s)", data["name"], f.name)
                else:
                    logger.warning("MG 动画 JSON 缺少 animation_id/name: %s", f.name)
            except Exception as e:
                logger.warning("MG 动画加载失败 %s: %s", f.name, e)

    return mg_anims


class MGAnimationPlugin(BasePlugin):
    """MG 动画插件 — 注册运动图形动画到 AnimationRegistry 和 HookRegistry。"""

    def initialize(self) -> None:
        """注册所有 MG 动画。"""
        plugin_dir = Path(__file__).resolve().parent
        animations_dir = plugin_dir / "animations"
        mg_anims = _load_mg_animations(animations_dir)

        if not mg_anims:
            logger.warning("MG 动画插件: 未找到任何动画定义")
            return

        from clipwright.animation.registry import AnimationRegistry

        registered_count = 0

        for mg_def in mg_anims:
            anim_id = mg_def["animation_id"]
            anim_name = mg_def["name"]
            duration = mg_def.get("duration_sec", 3.0)

            # 1. 注册为 ONSCREEN 类型，使 AnimationRegistry 能查到
            # MG 关键帧时间是绝对秒数，需归一化到 0-1
            first_kfs = []
            if mg_def.get("elements"):
                first_elem = mg_def["elements"][0]
                for kf in first_elem.get("keyframes", [])[:2]:
                    norm_time = min(1.0, kf["time"] / max(duration, 0.01))
                    first_kfs.append(Keyframe(
                        time=norm_time,
                        properties={k: v for k, v in kf.items() if k != "time"},
                    ))

            anim_def = AnimationDef(
                animation_id=anim_id,
                name=anim_name,
                type=AnimationType.ONSCREEN,
                target=AnimationTarget.ANY,
                duration_sec=duration,
                easing=EasingFunction.EASE_OUT,
                keyframes=first_kfs,
                description=mg_def.get("description", ""),
                properties_meta={
                    "mg_json": PropertyDef(
                        type="string", default="",
                        description="MG 动画 JSON 定义（插件加载时自动注入）",
                    ),
                },
            )
            AnimationRegistry.register(anim_def, plugin_id="mg_animations")
            registered_count += 1

        # 2. 通过 DIAGRAM_RENDERER_EXTEND hook 注册为逻辑动画类型
        #    使 AnimationCatalog.get_logic_animations() 能返回它们
        def _mg_renderer_extender(ctx: dict) -> dict:
            renderers = ctx.get("renderers", [])
            plugin_dir_local = Path(__file__).resolve().parent
            mg_data = _load_mg_animations(plugin_dir_local / "animations")
            for mg in mg_data:
                aid = mg["animation_id"]
                # 检查是否已注册
                if any(r.get("id") == aid for r in renderers):
                    continue
                renderers.append({
                    "id": aid,
                    "name": mg["name"],
                    "desc": mg.get("description", ""),
                    "category": "mg",
                })
            ctx["renderers"] = renderers
            return ctx

        HookRegistry.register(HookPoint.DIAGRAM_RENDERER_EXTEND, _mg_renderer_extender)

        # 3. 通过 ANIMATION_CATALOG_EXTEND hook 在逻辑动画列表中注册
        def _mg_catalog_extender(ctx: dict) -> dict:
            extensions = ctx.get("extensions", [])
            plugin_dir_local = Path(__file__).resolve().parent
            mg_data = _load_mg_animations(plugin_dir_local / "animations")
            for mg in mg_data:
                aid = mg["animation_id"]
                if any(e.get("id") == aid for e in extensions):
                    continue
                extensions.append({
                    "id": aid,
                    "name": mg["name"],
                    "category": "logic",
                    "desc": mg.get("description", ""),
                })
            ctx["extensions"] = extensions
            return ctx

        HookRegistry.register(HookPoint.ANIMATION_CATALOG_EXTEND, _mg_catalog_extender)

        logger.info(
            "MG 动画插件初始化完成: %d 个动画已注册, %d 个 renderer hooks, %d 个 catalog hooks",
            registered_count, 1, 1,
        )
