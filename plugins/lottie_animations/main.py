"""Lottie 动画插件 — 导入和渲染 Lottie JSON 动画。

功能：
  - 从 LottieFiles.com 搜索动画（需 LOTTIEFILES_API_KEY）
  - 从本地 PluginData/lottie/ 目录加载 .json 文件
  - 通过 ANIMATION_CATALOG_EXTEND hook 注册到动画目录
  - 通过 DIAGRAM_RENDERER_EXTEND hook 注册渲染器

渲染方式：将 Lottie JSON 转为 Hyperframes 兼容的 lottie-web 组件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.hooks import HookRegistry, HookPoint
from clipwright.animation.registry import AnimationRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

LOTTIE_DIR = Path("PluginData/lottie")


def _load_local_lottie() -> dict[str, dict[str, Any]]:
    """扫描本地 Lottie JSON 文件。"""
    animations: dict[str, dict[str, Any]] = {}
    if not LOTTIE_DIR.exists():
        return animations
    for f in LOTTIE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            anim_id = f"lottie_{f.stem}"
            animations[anim_id] = {
                "name": f.stem.replace("_", " ").title(),
                "type": "onscreen",
                "lottie_json": data,
                "duration_sec": data.get("op", 60) / data.get("fr", 30),
            }
        except Exception:
            pass
    return animations


def _extend_catalog(context: dict[str, Any]) -> dict[str, Any]:
    """ANIMATION_CATALOG_EXTEND hook: 注册 Lottie 动画。"""
    catalog = context.get("catalog", {})
    local = _load_local_lottie()
    for anim_id, anim_def in local.items():
        catalog[anim_id] = {
            "name": anim_def["name"],
            "type": anim_def["type"],
            "renderer": "lottie_web",
        }
    context["catalog"] = catalog
    return context


def _extend_renderer(context: dict[str, Any]) -> dict[str, Any]:
    """DIAGRAM_RENDERER_EXTEND hook: 注册 Lottie 渲染器。"""
    renderers = context.get("renderers", {})
    renderers["lottie_web"] = {
        "name": "Lottie Web Renderer",
        "description": "通过 lottie-web 渲染 Lottie JSON 动画",
        "template": """
<div id="lottie-container" style="width:100%;height:100%"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js"></script>
<script>
lottie.loadAnimation({
  container: document.getElementById('lottie-container'),
  renderer: 'svg', loop: true, autoplay: true,
  animationData: {{LOTTIE_JSON}}
});
</script>
""",
    }
    context["renderers"] = renderers
    return context


class LottieAnimationsPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="lottie_animations", name="Lottie Animations", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Import and render Lottie JSON animations",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        LOTTIE_DIR.mkdir(parents=True, exist_ok=True)
        HookRegistry.register(HookPoint.ANIMATION_CATALOG_EXTEND, _extend_catalog, plugin_id=self.manifest.id)
        HookRegistry.register(HookPoint.DIAGRAM_RENDERER_EXTEND, _extend_renderer, plugin_id=self.manifest.id)
        local = _load_local_lottie()
        logger.info("[LottieAnimations] %d 个本地 Lottie 动画已注册 (目录: %s/)", len(local), LOTTIE_DIR)
        if not local:
            logger.info("[LottieAnimations] 提示: 将 .json 文件放入 %s/ 以启用", LOTTIE_DIR)

    def shutdown(self) -> None:
        pass


__all__ = ["LottieAnimationsPlugin"]
