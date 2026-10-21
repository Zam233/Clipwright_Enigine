"""plan-first 接线：把 ``PlanDocument`` 变成结构 Agent 的输入与漂移留痕（PRD §II.13 / A-16/A-17）。

三件事：
1. ``resolve_plan`` / ``require_plan``：从 ``context.extra_params`` 取规划；开了
   ``plan_first`` 却没有计划时**明确失败**（不许悄悄退回自由生成，验收 V55）；
2. ``plan_prompt_block``：把段落目的 / 画面类型 / 卡点策略渲染成可注入的文本块；
3. ``detect_drift`` / ``append_drift``：执行偏离规划时留痕（谁改了哪一段、为什么）。

全部纯函数 + 一个小 JSONL 追加器，便于单测。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.schema.category_spec import PlanDocument


class PlanMissing(RuntimeError):
    """开启了 plan-first 但没有可用规划。"""


def _coerce(raw: Any) -> Optional[PlanDocument]:
    if raw is None:
        return None
    if isinstance(raw, PlanDocument):
        return raw
    if isinstance(raw, dict):
        try:
            return PlanDocument(**raw)
        except Exception:  # noqa: BLE001 - 坏 plan 视为缺失，由 require_plan 报
            return None
    return None


def resolve_plan(extra_params: dict[str, Any] | None) -> Optional[PlanDocument]:
    """从 ``extra_params`` 取规划（支持 ``_plan`` / ``plan`` / ``production_plan.plan``）。"""
    extra = extra_params or {}
    for key in ("_plan", "plan"):
        plan = _coerce(extra.get(key))
        if plan is not None:
            return plan
    nested = extra.get("production_plan")
    if isinstance(nested, dict):
        plan = _coerce(nested.get("plan"))
        if plan is not None:
            return plan
    return None


def is_plan_first(extra_params: dict[str, Any] | None) -> bool:
    return bool((extra_params or {}).get("plan_first", False))


def require_plan(extra_params: dict[str, Any] | None) -> Optional[PlanDocument]:
    """plan-first 模式下要求必须有规划；未开启时返回 None（零行为变化）。"""
    plan = resolve_plan(extra_params)
    if plan is not None:
        return plan
    if is_plan_first(extra_params):
        raise PlanMissing(
            "plan_first=True 但没有可用规划：请先产出并确认 PlanDocument"
            "（空 plan 不允许退回自由生成，见 PRD §II.13.4 / V55）"
        )
    return None


def plan_prompt_block(plan: PlanDocument) -> str:
    """把规划渲染成注入结构 prompt 的文本块（固定顺序、确定性）。"""
    if plan is None or not plan.sections:
        return ""
    lines = [f"## 已确认的整体规划（类型：{plan.type or '未指定'}）", ""]
    lines.append(f"- 目标时长：{float(plan.target_duration_sec):.2f}s")
    if plan.screen_kinds:
        lines.append(f"- 画面类型：{'、'.join(plan.screen_kinds)}")
    lines.append("")
    lines.append("段落规划（场景划分必须落在这些段落内）：")
    for index, section in enumerate(plan.sections, 1):
        screen = "、".join(section.screen_kinds) if section.screen_kinds else "（未指定）"
        lines.append(
            f"{index}. [{section.id}] {section.name or ''} "
            f"{float(section.t0):.2f}–{float(section.t1):.2f}s；"
            f"目的：{section.purpose or '（未填写）'}；画面：{screen}"
        )
        for event in section.key_events:
            moment = event.get("t")
            what = event.get("what") or event.get("name") or ""
            if isinstance(moment, (int, float)):
                lines.append(f"   - 事件 {float(moment):.3f}s：{what}")
    if plan.beats is not None and plan.beats.bpm > 0:
        lines.append("")
        lines.append(
            f"卡点策略：BPM {plan.beats.bpm:g}，第 0 拍 {float(plan.beats.beat_zero_sec):.3f}s；"
            f"{plan.beats.policy or '切点落在拍或半拍'}"
        )
    if plan.material_strategy is not None and plan.material_strategy.gaps:
        lines.append("")
        lines.append("素材缺口（场景描述里必须标注「需补充素材」而不是假设有）：")
        lines.extend(f"- {gap}" for gap in plan.material_strategy.gaps)
    lines.append("")
    lines.append("要求：场景的时间与目的必须与上表一致；偏离必须在执行记录里留痕（plan_drift）。")
    return "\n".join(lines)


def detect_drift(
    plan: PlanDocument,
    executed: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对比规划与实际场景，找出偏离（时间/数量/画面类型）。

    ``executed`` 为场景列表（每项至少含 ``duration_sec``，可选 ``id``/``screen_kinds``）。
    """
    if plan is None:
        return []
    executed = list(executed or [])
    drifts: list[dict[str, Any]] = []

    planned_total = sum(max(0.0, float(s.t1) - float(s.t0)) for s in plan.sections)
    executed_total = sum(float(s.get("duration_sec") or 0.0) for s in executed)
    if plan.sections and executed:
        tolerance = max(0.5, 0.10 * max(planned_total, 1e-6))
        if abs(executed_total - planned_total) > tolerance:
            drifts.append({
                "code": "plan_drift_duration",
                "planned": round(planned_total, 3),
                "executed": round(executed_total, 3),
                "delta": round(executed_total - planned_total, 3),
            })

    planned_count = len(plan.sections)
    if planned_count and executed and len(executed) != planned_count:
        drifts.append({
            "code": "plan_drift_section_count",
            "planned": planned_count,
            "executed": len(executed),
        })

    planned_kinds = set(plan.screen_kinds or [])
    if planned_kinds:
        for index, scene in enumerate(executed):
            kinds = set(scene.get("screen_kinds") or [])
            if kinds and not (kinds & planned_kinds):
                drifts.append({
                    "code": "plan_drift_screen_kind",
                    "index": index,
                    "planned": sorted(planned_kinds),
                    "executed": sorted(kinds),
                })
    return drifts


def append_drift(path: str | Path, entries: Iterable[dict[str, Any]]) -> int:
    """把漂移记录追加到 JSONL（一行一条，便于事后查"谁改了哪一段"）。"""
    entries = list(entries or [])
    if not entries:
        return 0
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return len(entries)


def emit_drift_events(
    pipeline_id: str,
    drifts: Iterable[dict[str, Any]],
    *,
    agent: str = "structure",
) -> int:
    """V58：把执行偏离写成 **trace 事件**（除 JSONL 之外的第二个可查入口）。

    为什么两条路都留：JSONL 便于跨管线汇总审计，trace 事件便于前端在运行详情里
    直接看到"这段为什么跟规划不一样"。任一通道失败都不得影响出片。
    """
    items = list(drifts or [])
    if not pipeline_id or not items:
        return 0
    try:
        from clipwright.services.trace import add_event
    except Exception:  # noqa: BLE001 - trace 不可用时不阻断
        return 0
    written = 0
    for item in items:
        try:
            add_event(
                pipeline_id,
                agent,
                "plan_drift",
                f"执行偏离规划：{item.get('code')}",
                detail=item,
            )
            written += 1
        except Exception:  # noqa: BLE001 - 单条失败继续
            continue
    return written


__all__ = [
    "PlanMissing",
    "append_drift",
    "detect_drift",
    "emit_drift_events",
    "is_plan_first",
    "plan_prompt_block",
    "require_plan",
    "resolve_plan",
]
