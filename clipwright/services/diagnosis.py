"""诊断书（PRD F5 / 开发计划 B2）——模板化生成，不调用 LLM。

为什么模板化：
- 诊断书是给**人**看的一页结论，需要稳定、可回归、成本为零；
- 归因已经由 `services/attribution.py` 完成，这里只负责「把事实排成人能读的顺序」；
- B2 阶段先交付 v1（模板），LLM 润色属于后续可选增强，不进本期验收。

结构（PRD §8 `DiagnosisReport`）：conclusion / problems / evidence / attempted /
suggestions / cost。
"""

from __future__ import annotations

from typing import Any, Sequence

from clipwright.schema.supervisor import Attribution, DiagnosisReport

from .attribution import STAGE_CN, STAGE_ORDER

_MAX_PROBLEMS = 12


def _entries_of(ledger_or_entries: Any) -> list[Any]:
    if ledger_or_entries is None:
        return []
    if hasattr(ledger_or_entries, "entries"):
        return list(ledger_or_entries.entries())
    if isinstance(ledger_or_entries, (list, tuple)):
        return list(ledger_or_entries)
    return []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _rank(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER)


def _cn(stage: str) -> str:
    return STAGE_CN.get(stage, stage or "未知阶段")


def build_diagnosis(
    ledger_or_entries: Any,
    *,
    attribution: Attribution | dict[str, Any] | None = None,
    issues: Sequence[dict[str, Any]] | None = None,
    mode: str = "shadow",
    pipeline_id: str = "",
    budget_usd: float | None = None,
) -> DiagnosisReport:
    """由账本 + 归因 + 质检问题生成诊断书。"""
    entries = _entries_of(ledger_or_entries)
    attr = attribution if isinstance(attribution, Attribution) else None
    if attr is None and isinstance(attribution, dict):
        try:
            attr = Attribution.model_validate(attribution)
        except Exception:  # noqa: BLE001
            attr = None

    failures = [e for e in entries if _get(e, "failure") is not None]
    degradations = [
        (_get(e, "stage", ""), d)
        for e in entries
        for d in (_get(e, "degradations", []) or [])
    ]

    problems: list[dict[str, Any]] = []
    for e in failures[:_MAX_PROBLEMS]:
        failure = _get(e, "failure")
        location = _get(failure, "location", {}) or {}
        problems.append({
            "severity": "error",
            "title": str(_get(failure, "blocking_point", "") or _get(e, "stage", "")),
            "location": _location_text(location),
            "stage": _get(e, "stage", ""),
            "category": str(_get(failure, "category", "")),
        })
    for stage, d in degradations[: max(0, _MAX_PROBLEMS - len(problems))]:
        problems.append({
            "severity": "warning",
            "title": str(_get(d, "detail", "") or _get(d, "kind", "")),
            "location": str(_get(d, "scope", "") or ""),
            "stage": stage,
            "category": str(_get(d, "kind", "")),
        })

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in (attr.evidence_refs if attr else []) or []:
        if ref and ref not in seen:
            seen.add(ref)
            evidence.append({"text": _evidence_text(ref, entries), "ref": ref})
    for e in failures:
        ref = str(_get(e, "id", "") or "")
        if ref and ref not in seen and len(evidence) < 5:
            seen.add(ref)
            evidence.append({"text": _evidence_text(ref, entries), "ref": ref})

    attempted: list[dict[str, Any]] = []
    for e in sorted(entries, key=lambda x: (_rank(str(_get(x, "stage", ""))), int(_get(x, "attempt", 0) or 0))):
        attempt = int(_get(e, "attempt", 0) or 0)
        if attempt < 2:
            continue
        stage = str(_get(e, "stage", ""))
        outcome = "成功" if _get(e, "failure") is None else "仍失败"
        attempted.append({
            "action": f"{_cn(stage)}第 {attempt} 次尝试",
            "outcome": outcome,
        })
    for e in entries:
        stage = str(_get(e, "stage", ""))
        if int(_get(e, "attempt", 1) or 1) < 2:
            continue
        model = str(_get(e, "model", "") or "")
        if model:
            attempted.append({"action": f"{_cn(stage)}使用模型 {model}", "outcome": "已执行"})
            break

    suggestions = _suggestions(attr, entries, failures, degradations, issues)

    tokens = 0
    for e in entries:
        usage = _get(e, "usage", {}) or {}
        tokens += int(_get(usage, "input_tokens", 0) or 0) + int(_get(usage, "output_tokens", 0) or 0)

    return DiagnosisReport(
        conclusion=_conclusion(attr, failures, degradations),
        problems=problems,
        evidence=evidence,
        attempted=attempted[:8],
        suggestions=suggestions,
        cost={"tokens": tokens, "spent_usd": None, "budget_usd": budget_usd, "subagent_tokens": 0},
        mode=mode,
        pipeline_id=pipeline_id,
    )


def _location_text(location: dict[str, Any]) -> str:
    if not isinstance(location, dict) or not location:
        return ""
    parts = [f"{k}={v}" for k, v in location.items() if v not in (None, "")]
    return "、".join(parts)[:120]


def _evidence_text(ref: str, entries: Sequence[Any]) -> str:
    for e in entries:
        if str(_get(e, "id", "")) != ref:
            continue
        stage = str(_get(e, "stage", ""))
        attempt = int(_get(e, "attempt", 0) or 0)
        failure = _get(e, "failure")
        if failure is not None:
            return f"{_cn(stage)}第 {attempt} 次尝试失败：{_get(failure, 'blocking_point', '') or _get(failure, 'category', '')}"
        degs = _get(e, "degradations", []) or []
        if degs:
            return f"{_cn(stage)}第 {attempt} 次尝试降级：{_get(degs[0], 'detail', '') or _get(degs[0], 'kind', '')}"
        return f"{_cn(stage)}第 {attempt} 次尝试"
    return ref


def _conclusion(attr: Attribution | None, failures: Sequence[Any], degradations: Sequence[Any]) -> str:
    if attr is not None and attr.primary_stage:
        base = f"问题主要来自「{_cn(attr.primary_stage)}」：{attr.reason}"
        if attr.unrepairable:
            base += "。这是资源确实缺失，重试同一做法不会变好。"
        return base
    if not failures and not degradations:
        return "本次运行没有记录到失败或降级。"
    if failures:
        return "有失败记录，但证据不足以定位责任阶段，建议查看账本明细。"
    return "有降级记录（成品打了折扣），但不足以定位责任阶段。"


def _suggestions(
    attr: Attribution | None,
    entries: Sequence[Any],
    failures: Sequence[Any],
    degradations: Sequence[Any],
    issues: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    primary = attr.primary_stage if attr else ""

    if attr is not None and attr.unrepairable:
        out.append({
            "id": "s_accept_degraded",
            "label": "接受当前版本（降级已标注）",
            "action": "accept_degraded",
        })
    elif primary and (attr is None or attr.confidence != "low"):
        out.append({
            "id": "s_redo_stage",
            "label": f"重做「{_cn(primary)}」",
            "action": "redo_stage",
            "target_stage": primary,
        })

    retried = any(int(_get(e, "attempt", 0) or 0) >= 2 for e in entries)
    if retried and not (attr is not None and attr.unrepairable):
        out.append({
            "id": "s_retry_with_model",
            "label": "换一个模型再试该阶段",
            "action": "retry_with_model",
            "target_stage": primary,
        })

    if issues:
        out.append({
            "id": "s_review_issues",
            "label": f"查看质检问题（{len(issues)} 条）",
            "action": "manual",
        })
    else:
        out.append({
            "id": "s_manual",
            "label": "查看账本明细",
            "action": "manual",
        })

    return out[:3]
