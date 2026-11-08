"""Lottie 动画插件 — **本地 Lottie 素材目录**（渲染未接入，2026-11-08 审计降级）。

B8-T8.4 降级说明：旧实现贡献两个形状错误的钩子——
1. `animation_catalog_extend`：往 `ctx["catalog"]` 塞 `{name,type,renderer}`，
   但 engine 既不读这个 context 键，动画目录也只认 `AnimationDef`；
2. `diagram_renderer_extend`：返回一段带 **外链 CDN** 的 HTML 模板，
   而渲染链从不消费那个 context 键（且外链脚本违反预览"禁止执行外部 HTML"的边界）。

所以本插件**不再声称能渲染 Lottie**：只保留
- `setup()` 创建本地素材目录；
- 一条 structure 提示词，告诉模型"本地有哪些 Lottie 素材、当前不参与成片渲染"。

真正的 Lottie 渲染需要先在 engine 侧定义结构化 spec（类似 MG 的 `mg_def`），
再由插件实现渲染器——那是一次独立能力建设，不在本次审计整改范围内。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind

LOTTIE_DIR = Path("PluginData/lottie")


def _load_local_lottie() -> dict[str, dict[str, Any]]:
    """扫描本地 Lottie JSON 文件（只读清单，不含渲染语义）。"""
    animations: dict[str, dict[str, Any]] = {}
    if not LOTTIE_DIR.exists():
        return animations
    for f in LOTTIE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            animations[f.stem] = {
                "name": f.stem.replace("_", " ").title(),
                "frames": data.get("op", 0),
                "frame_rate": data.get("fr", 0),
            }
        except Exception:  # noqa: BLE001 — 单个坏文件不影响其它素材
            continue
    return animations


class LottieAnimationsPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="lottie_animations", name="Lottie Animations", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="本地 Lottie 素材目录（渲染未接入）",
        author="Clipwright Team",
    )

    def setup(self) -> None:
        """副作用位（P1）：确保 Lottie 目录存在（与注册无关，须无条件执行）。"""
        LOTTIE_DIR.mkdir(parents=True, exist_ok=True)

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移；B8-T8.4 收窄）：只保留一条提示词。"""
        local = _load_local_lottie()
        logger.info("[LottieAnimations] %d 个本地 Lottie 素材 (目录: %s/)",
                    len(local), LOTTIE_DIR)
        names = "、".join(sorted(local)) if local else "无"
        prompt = (
            "## Lottie 素材（来自 lottie_animations 插件）\n"
            f"本地素材目录 `{LOTTIE_DIR}/` 现有：{names}。\n"
            "注意：**Lottie 渲染尚未接入成片链路**，不要为它们生成 [逻辑动画] 标记，"
            "也不要把 Lottie 当作可用动画类型；如需矢量动效用 MG 动画表达。"
        )
        return [
            Contribution(
                ContributionKind.PROMPT, "lottie_structure_prompt", prompt,
                meta={"agent": "structure", "prompt": prompt, "priority": 2},
            ),
        ]

    def shutdown(self) -> None:
        pass


__all__ = ["LottieAnimationsPlugin"]
