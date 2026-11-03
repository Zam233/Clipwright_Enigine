"""规划书确定性渲染（PRD §II.13.4，A-15）。

规划书 **不再由 LLM 自由写 Markdown**：同一份 ``PlanDocument`` 两次渲染必须逐字节
相同，才能 diff、审核、复现（验收 V52）。

本模块只做"结构化对象 → 文本"，不调 LLM、不含时间戳、不依赖字典遍历顺序。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.schema.category_spec import PlanDocument

_EMPTY = "—"


def _fmt_sec(value: float) -> str:
    return f"{float(value):.2f}"


def _cell(text: Any) -> str:
    """表格单元格转义：竖线与换行会破坏 Markdown 表格。"""
    raw = "" if text is None else str(text)
    return raw.replace("|", "／").replace("\n", " ").strip() or _EMPTY


def render_brief_markdown(brief: dict[str, Any]) -> str:
    """创意简报（brief.md）：为什么做、给谁看、红线是什么。"""
    lines: list[str] = ["# 创意简报", ""]
    fields = [
        ("标题", "title"),
        ("概述", "overview"),
        ("目标受众", "target_audience"),
        ("核心信息", "core_message"),
        ("风格方向", "style_direction"),
        ("结构建议", "structure_suggestion"),
        ("预估时长", "duration_estimate"),
    ]
    for label, key in fields:
        value = str(brief.get(key) or "").strip()
        if value:
            lines.append(f"- **{label}**：{value}")
    for label, key in (("关键元素", "key_elements"), ("特殊要求", "special_requirements"),
                       ("关键点", "key_points"), ("参考", "references")):
        items = brief.get(key) or []
        if isinstance(items, str):
            items = [items]
        items = [str(i).strip() for i in items if str(i).strip()]
        if items:
            lines.append(f"- **{label}**：")
            lines.extend(f"  - {item}" for item in items)
    return "\n".join(lines).rstrip() + "\n"


def render_plan_markdown(plan: PlanDocument, brief: Optional[dict[str, Any]] = None) -> str:
    """规划书（plan.md）：怎么做——段落、画面、卡点、素材、风险、验收。"""
    lines: list[str] = ["# 视频成片规划书", ""]

    title = str((brief or {}).get("title") or "").strip()
    if title:
        lines.append(f"- **标题**：{title}")
    lines.append(f"- **类型**：{_cell(plan.type)}")
    lines.append(f"- **目标时长**：{_fmt_sec(plan.target_duration_sec)}s")
    if plan.legacy:
        lines.append("- **来源**：由场景列表反向生成（legacy，非 plan-first 产物）")
    if plan.screen_kinds:
        lines.append(f"- **画面类型**：{'、'.join(plan.screen_kinds)}")
    lines.append("")

    lines.append("## 段落规划")
    lines.append("")
    if plan.sections:
        lines.append("| # | 段落 | 时间 | 目的 | 画面 |")
        lines.append("|---|------|------|------|------|")
        for index, section in enumerate(plan.sections, 1):
            screen = "、".join(section.screen_kinds) if section.screen_kinds else _EMPTY
            lines.append(
                f"| {index} | {_cell(section.name or section.id)} | "
                f"{_fmt_sec(section.t0)}–{_fmt_sec(section.t1)}s | "
                f"{_cell(section.purpose)} | {_cell(screen)} |"
            )
        lines.append("")
        for index, section in enumerate(plan.sections, 1):
            if not section.key_events:
                continue
            lines.append(f"### 段落 {index} 关键事件")
            lines.append("")
            for event in section.key_events:
                moment = event.get("t")
                what = event.get("what") or event.get("name") or ""
                stamp = f"{_fmt_sec(float(moment))}s" if isinstance(moment, (int, float)) else _EMPTY
                lines.append(f"- `{stamp}` {_cell(what)}")
            lines.append("")
    else:
        lines.append("_（无段落规划）_")
        lines.append("")

    if plan.beats is not None:
        lines.append("## 卡点策略")
        lines.append("")
        lines.append(
            f"- **BPM**：{float(plan.beats.bpm):g}；**第 0 拍**："
            f"{_fmt_sec(plan.beats.beat_zero_sec)}s；**一拍**："
            f"{_fmt_sec(plan.beats.beat_sec)}s"
        )
        if plan.beats.policy:
            lines.append(f"- **策略**：{plan.beats.policy}")
        lines.append("")

    if plan.material_strategy is not None:
        strategy = plan.material_strategy
        lines.append("## 素材策略")
        lines.append("")
        if strategy.sources:
            lines.append(f"- **来源**：{'、'.join(strategy.sources)}")
        if strategy.grounding:
            lines.append(f"- **落位规则**：{strategy.grounding}")
        if strategy.license_policy:
            lines.append(f"- **授权策略**：{strategy.license_policy}")
        if strategy.gaps:
            lines.append("- **素材缺口**（需补齐，否则会体现在成片里）：")
            lines.extend(f"  - {gap}" for gap in strategy.gaps)
        lines.append("")

    if plan.risks:
        lines.append("## 风险与缓解")
        lines.append("")
        for risk in plan.risks:
            lines.append(f"- **{_cell(risk.risk)}** → {_cell(risk.mitigation)}")
        lines.append("")

    if plan.acceptance:
        lines.append("## 验收指标")
        lines.append("")
        lines.append("| 指标 | 目标 |")
        lines.append("|------|------|")
        for item in plan.acceptance:
            lines.append(f"| {_cell(item.metric)} | {_cell(item.target)} |")
        lines.append("")

    if plan.custom_sections:
        lines.append("## 自定义维度")
        lines.append("")
        for section in plan.custom_sections:
            lines.append(f"### {_cell(section.name)}")
            lines.append("")
            lines.append(f"- **理由**：{_cell(section.reason)}")
            if section.content:
                lines.append(f"- **内容**：{_cell(section.content)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_brief_markdown", "render_plan_markdown"]
