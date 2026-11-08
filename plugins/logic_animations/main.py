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
    # B8-T8.2 第二步：11 个渲染器已从 engine 搬进本插件（键名与 RENDERER_MAP 一致），
    # 所以这里列 12 种是**实现面**而不是口号；禁用插件后 `DiagramRenderer.get_supported_presets()`
    # 里不再有这些 id。
    "mindmap": "中心节点 → 多级辐射分支，适合知识体系/头脑风暴",
    "radar": "3-8 轴多维度能力对比，支持数值标注",
    "gantt": "项目时间规划，横向条状图 + 依赖关系",
    "venn3": "三者交集关系，7 区域标注",
    "heatmap": "矩阵色块数据分布，自动配色映射",
    "sankey": "流量/转化/资金流向，宽度可变路径",
    "concept": "自由节点 + 任意方向连线图，适合知识连接",
    "codeblock": "等宽字体 + 行号 + 语法高亮，适合编程教程",
    "datatable": "结构化表格，交替底色 + 表头",
    "quote": "引用卡片，装饰引号 + 作者署名",
    "compcard": "左右双列详细对比，高亮胜出方",
    "orgchart": "自上而下组织结构图，缩进表层级",
}


class LogicAnimationsPlugin(CapabilityPlugin):
    """高级逻辑图解插件。"""
    manifest = PluginManifest(
        id="logic_animations",
        name="高级逻辑图解插件",
        version="2.0.0",
        kind=PluginKind.CAPABILITY,
        description="12 种图解渲染器：思维导图/雷达/甘特/维恩3/热力/桑基/概念/代码块/数据表/引用/对比卡/组织架构",
    )

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：图解渲染钩子 + 给 structure Agent 的提示词片段。"""
        desc_list = "\n".join(f"  - {fid} — {desc}" for fid, desc in _DESCRIPTIONS.items())
        prompt = (
            "## 高级图解类型（来自 logic_animations 插件）\n"
            f"本插件渲染以下 12 种图解（可通过 mg_dynamic 或 [逻辑动画] 调用）：\n{desc_list}"
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
