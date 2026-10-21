"""规划校验器（PRD §II.13.5，A-13）。

职责：把"动态规划"关进可验收的笼子。
- 必填维度缺失 → 拒绝确认（``plan_missing_required``）；
- 未知维度且 ``custom_allowed=false`` → 拒绝（``plan_unknown_dimension``）；
- 段落必须覆盖 ``[0, target_duration_sec]``，不得重叠/留空；
- 时长偏差 >10% 告警、>25% 拒绝；
- 有 ``beats`` 时必须与音频网格一致（BPM / 第 0 拍）；
- ``custom_sections`` 每项必须写 reason。

本模块纯函数，供 API（``plan_ready → plan_confirmed`` 门禁）、Agent 与单测共用。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from clipwright.schema.category_spec import PLAN_DIMENSIONS, PlanDocument, PlanSchema

#: 段落拼接容差（秒）：逐段浮点累加允许的误差。
SECTION_EPS = 1e-3
#: 时长偏差阈值。
DRIFT_WARN = 0.10
DRIFT_REJECT = 0.25


class PlanIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    where: str = ""


class PlanValidation(BaseModel):
    ok: bool = True
    issues: list[PlanIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def _dimension_present(plan: PlanDocument, name: str) -> bool:
    value = getattr(plan, name, None)
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    return True


def validate_plan(
    plan: PlanDocument,
    schema: Optional[PlanSchema] = None,
    *,
    audio_grid: Optional[dict[str, float]] = None,
) -> PlanValidation:
    """对一份 ``PlanDocument`` 做全量校验。"""
    schema = schema or PlanSchema()
    issues: list[PlanIssue] = []

    # 1) 必填/可选维度
    for name in schema.required:
        if not _dimension_present(plan, name):
            issues.append(
                PlanIssue(
                    code="plan_missing_required",
                    message=f"类型要求的规划维度 {name} 缺失或为空",
                    where=name,
                )
            )
    known = set(PLAN_DIMENSIONS) | set(schema.required) | set(schema.optional)
    if plan.custom_sections and not schema.custom_allowed:
        issues.append(
            PlanIssue(
                code="plan_unknown_dimension",
                message=(
                    "该类型不允许自定义规划维度，但规划里出现 "
                    f"{[s.name for s in plan.custom_sections]}"
                ),
                where="custom_sections",
            )
        )
    for name in schema.required:
        if name not in known:  # pragma: no cover - 类型作者写错维度名
            issues.append(
                PlanIssue(
                    code="plan_unknown_dimension_def",
                    message=f"plan_schema.required 里的 {name} 不是已知规划维度",
                    where=name,
                )
            )

    # 2) 段落连续性
    if plan.sections:
        ordered = sorted(plan.sections, key=lambda s: (s.t0, s.t1))
        prev_end: Optional[float] = None
        for section in ordered:
            if section.t1 <= section.t0:
                issues.append(
                    PlanIssue(
                        code="plan_section_invalid",
                        message=f"段落 {section.id} 的 t1({section.t1}) 不大于 t0({section.t0})",
                        where=section.id,
                    )
                )
            if prev_end is not None and section.t0 > prev_end + SECTION_EPS:
                issues.append(
                    PlanIssue(
                        code="plan_section_gap",
                        message=(
                            f"段落 {section.id} 与上一段之间有空洞："
                            f"{prev_end} → {section.t0}"
                        ),
                        where=section.id,
                    )
                )
            if prev_end is not None and section.t0 < prev_end - SECTION_EPS:
                issues.append(
                    PlanIssue(
                        code="plan_section_overlap",
                        message=(
                            f"段落 {section.id} 与上一段重叠：{section.t0} < {prev_end}"
                        ),
                        where=section.id,
                    )
                )
            prev_end = max(prev_end or section.t1, section.t1)
        if ordered[0].t0 > SECTION_EPS:
            issues.append(
                PlanIssue(
                    code="plan_section_gap",
                    message=f"首段未从 0 开始（t0={ordered[0].t0}）",
                    where=ordered[0].id,
                )
            )
        target = float(plan.target_duration_sec or 0)
        if target > 0 and prev_end is not None:
            drift = abs(prev_end - target) / target
            severity = "error" if drift > DRIFT_REJECT else ("warning" if drift > DRIFT_WARN else "")
            if severity:
                issues.append(
                    PlanIssue(
                        code="plan_duration_drift",
                        message=(
                            f"段落总长 {prev_end:.2f}s 与目标 {target:.2f}s 偏差 "
                            f"{drift:.0%}（阈值 {DRIFT_WARN:.0%} 告警 / {DRIFT_REJECT:.0%} 拒绝）"
                        ),
                        severity=severity,
                        where="target_duration_sec",
                    )
                )

    # 3) 卡点自洽
    if plan.beats is not None and audio_grid:
        bpm = float(audio_grid.get("bpm") or 0)
        beat_zero = float(audio_grid.get("beat_zero_sec") or 0)
        if bpm > 0 and abs(float(plan.beats.bpm) - bpm) > 1e-6:
            issues.append(
                PlanIssue(
                    code="plan_beats_mismatch",
                    message=f"规划 BPM {plan.beats.bpm} 与音频 BPM {bpm} 不一致",
                    where="beats.bpm",
                )
            )
        if abs(float(plan.beats.beat_zero_sec) - beat_zero) > 1e-3:
            issues.append(
                PlanIssue(
                    code="plan_beats_mismatch",
                    message=(
                        f"规划第 0 拍 {plan.beats.beat_zero_sec}s 与音频 "
                        f"{beat_zero}s 不一致"
                    ),
                    where="beats.beat_zero_sec",
                )
            )

    # 4) 自定义维度必须写理由
    for section in plan.custom_sections:
        if not (section.reason or "").strip():
            issues.append(
                PlanIssue(
                    code="plan_missing_reason",
                    message=f"自定义维度 {section.name} 缺少 reason",
                    where=section.name,
                )
            )

    return PlanValidation(ok=not any(i.severity == "error" for i in issues), issues=issues)


def assert_plan_confirmable(
    plan: PlanDocument,
    schema: Optional[PlanSchema] = None,
    *,
    audio_grid: Optional[dict[str, float]] = None,
) -> None:
    """``plan_ready → plan_confirmed`` 的门禁；不可确认时抛 ValueError。"""
    result = validate_plan(plan, schema, audio_grid=audio_grid)
    if not result.ok:
        detail = "; ".join(f"{i.code}: {i.message}" for i in result.errors[:5])
        raise ValueError(f"规划不可确认：{detail}")


def plan_from_scenes(scenes: list[dict[str, Any]], type_id: str = "") -> PlanDocument:
    """老数据兼容（V60）：由场景列表反向生成 legacy 规划。"""
    sections = []
    cursor = 0.0
    for index, scene in enumerate(scenes or [], 1):
        duration = float(scene.get("duration_sec") or scene.get("duration") or 0)
        section_id = str(scene.get("id") or f"S{index}")
        sections.append(
            {
                "id": section_id,
                "name": str(scene.get("title") or ""),
                "t0": cursor,
                "t1": cursor + duration,
                "purpose": str(scene.get("description") or ""),
                "screen_kinds": [],
            }
        )
        cursor += duration
    return PlanDocument(
        type=type_id,
        target_duration_sec=cursor,
        sections=sections,  # type: ignore[arg-type]
        material_strategy={
            "sources": [],
            "grounding": "",
            "gaps": [],
            "license_policy": "",
        },
        legacy=True,
    )


__all__ = [
    "DRIFT_REJECT",
    "DRIFT_WARN",
    "PlanIssue",
    "PlanValidation",
    "assert_plan_confirmable",
    "plan_from_scenes",
    "validate_plan",
]
