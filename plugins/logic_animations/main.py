"""LogicAnimations 插件入口 — 通过 Hook 注册 12 种高级图解。"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.plugins.base import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind
from .diagrams.all import RENDERER_MAP


def register_diagrams(context: dict[str, Any]) -> dict[str, Any]:
    """Hook 回调：注册所有图解类型到 DIAGRAM_RENDERER_EXTEND。"""
    renderers = []
    for fid, (name, fn) in RENDERER_MAP.items():
        renderers.append({
            "id": fid,
            "name": name,
            "desc": _DESCRIPTIONS.get(fid, ""),
            "renderer": fn,
        })
    return {"renderers": renderers}


_DESCRIPTIONS: dict[str, str] = {
    # 只列本插件**真正渲染**的类型。其余 11 种（radar/gantt/venn3/heatmap/sankey/
    # concept/codeblock/datatable/quote/compcard/orgchart）由 engine 的
    # `DiagramRenderer` 内置渲染，插件不再声称拥有它们（B8-T8.2 诚实化）。
    "mindmap": "中心节点 → 多级辐射分支，适合知识体系/头脑风暴",
}


class LogicAnimationsPlugin(CapabilityPlugin):
    """高级逻辑图解插件。"""
    manifest = PluginManifest(
        id="logic_animations",
        name="高级逻辑图解插件",
        version="2.0.0",
        kind=PluginKind.CAPABILITY,
        description="思维导图（mindmap）渲染器；其余 11 种图解由 engine 内置",
    )

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：图解渲染钩子 + 给 structure Agent 的提示词片段。"""
        desc_list = "\n".join(f"  - {fid} — {desc}" for fid, desc in _DESCRIPTIONS.items())
        prompt = (
            "## 高级图解类型（来自 logic_animations 插件）\n"
            f"本插件提供以下图解（可通过 mg_dynamic 或 [逻辑动画] 调用）：\n{desc_list}\n"
            "注意：radar/gantt/venn3/heatmap/sankey/concept/codeblock/datatable/quote/"
            "compcard/orgchart 是引擎内置图解，不需要本插件，也不要声称它们来自插件。"
        )
        return [
            Contribution(
                ContributionKind.HOOK, "logic_animations_renderer",
                register_diagrams, meta={"point": "diagram_renderer_extend"},
            ),
            Contribution(
                ContributionKind.PROMPT, "logic_animations_structure_prompt", prompt,
                meta={"agent": "structure", "prompt": prompt, "priority": 3},
            ),
        ]
