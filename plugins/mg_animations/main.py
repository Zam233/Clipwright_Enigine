"""MG 动画插件 — 自定义运动图形动画，JSON 格式定义，注册为动画与目录扩展。

P1 迁移：由 `initialize()` 手写注册改为新契约 `contributions()`（声明式）。
两个 hook 从闭包提为模块级函数，便于随声明一起交给门面注册。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
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

_ANIMATIONS_DIR = Path(__file__).resolve().parent / "animations"


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


def _mg_renderer_extender(ctx: dict) -> dict:
    """DIAGRAM_RENDERER_EXTEND：把 MG 动画登记为逻辑图解渲染器。"""
    renderers = ctx.get("renderers", [])
    for mg in _load_mg_animations(_ANIMATIONS_DIR):
        aid = mg["animation_id"]
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


def _mg_catalog_extender(ctx: dict) -> dict:
    """ANIMATION_CATALOG_EXTEND：把 MG 动画加入逻辑动画目录。"""
    extensions = ctx.get("extensions", [])
    for mg in _load_mg_animations(_ANIMATIONS_DIR):
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


def _build_anim_def(mg_def: dict[str, Any]) -> AnimationDef:
    """JSON 定义 → AnimationDef（首个元素的头两帧归一化到 0-1）。"""
    duration = mg_def.get("duration_sec", 3.0)
    first_kfs: list[Keyframe] = []
    if mg_def.get("elements"):
        first_elem = mg_def["elements"][0]
        for kf in first_elem.get("keyframes", [])[:2]:
            norm_time = min(1.0, kf["time"] / max(duration, 0.01))
            first_kfs.append(Keyframe(
                time=norm_time,
                properties={k: v for k, v in kf.items() if k != "time"},
            ))
    return AnimationDef(
        animation_id=mg_def["animation_id"],
        name=mg_def["name"],
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


class MGAnimationPlugin(BasePlugin):
    """MG 动画插件 — 以新契约贡献动画定义与两个目录扩展钩子。"""

    def contributions(self) -> list[Any]:
        """JSON 里的每个 MG 动画 + 两个目录扩展 hook。"""
        items: list[Any] = [
            Contribution(
                ContributionKind.HOOK, "mg_renderer_extend", _mg_renderer_extender,
                meta={"point": "diagram_renderer_extend"},
            ),
            Contribution(
                ContributionKind.HOOK, "mg_catalog_extend", _mg_catalog_extender,
                meta={"point": "animation_catalog_extend"},
            ),
        ]
        mg_anims = _load_mg_animations(_ANIMATIONS_DIR)
        if not mg_anims:
            logger.warning("MG 动画插件: 未找到任何动画定义（仅贡献目录扩展钩子）")
            return items
        for mg_def in mg_anims:
            anim_def = _build_anim_def(mg_def)
            items.append(Contribution(
                ContributionKind.ANIMATION, anim_def.animation_id, anim_def,
            ))
        return items


__all__ = ["MGAnimationPlugin"]
