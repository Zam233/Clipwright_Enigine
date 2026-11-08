"""归因裁定（PRD F2 / 开发计划 B2）。

设计原则（与计划 §4.3 对齐）：

1. **规则优先**：R1 资源缺失 → R3 上游申报 → R2 上游失败未消化 → R4 静态类别表。
   规则是确定性的、可测的，覆盖大多数情形。
2. **LLM 只在规则全不命中时兜底**（`attribute_with_llm`），输入只有账本摘要与
   质检症状，不给它可执行权限。
3. **置信度低就不硬派工**：`confidence=low` 时 B3 会升级给人而不是盲目重做——这
   一条是「归因错了比重做错阶段更贵」的落地。

阶段顺序与 `services.stage_closure.STAGE_DEPS` 保持一致（structure→…→quality）；
M3 删除老编排器后，`stage_closure` 是依赖关系的唯一真值。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from clipwright.config import logger
from clipwright.schema.supervisor import Attribution

STAGE_ORDER: tuple[str, ...] = ("structure", "material", "edit", "animation", "audio", "quality")

STAGE_CN: dict[str, str] = {
    "structure": "脚本结构",
    "material": "素材选材",
    "edit": "粗剪",
    "animation": "动画",
    "audio": "音频",
    "quality": "质检",
}

#: 视为「资源确实缺失」的降级类型（重试无意义）
RESOURCE_KINDS: tuple[str, ...] = (
    "resource_missing",
    "material_placeholder",
    "empty_result",
    "no_candidates",
    "source_unavailable",
)

#: 与 `agents/quality_agent.py` 的「类别 → 责任阶段」映射保持一致（R4 兜底用）
CATEGORY_TO_STAGE: dict[str, str] = {
    "material": "material",
    "material_match": "material",
    "structure": "edit",
    "duration": "edit",
    "rhythm": "edit",
    "semantic": "edit",
    "animation": "animation",
    "transition": "animation",
    "audio": "audio",
}


def _entries_of(ledger_or_entries: Any) -> list[Any]:
    """接受 EpisodeLedger / list[LedgerEntry] / list[dict]。"""
    if ledger_or_entries is None:
        return []
    if hasattr(ledger_or_entries, "entries"):
        return list(ledger_or_entries.entries())
    if isinstance(ledger_or_entries, (list, tuple)):
        return list(ledger_or_entries)
    return []


def _get(entry: Any, name: str, default: Any = None) -> Any:
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _stage_rank(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER)


def _sorted_stages(stages: Iterable[str]) -> list[str]:
    return sorted({s for s in stages if s in STAGE_ORDER}, key=_stage_rank)


def _issue_stage(issues: Sequence[dict[str, Any]] | None) -> str:
    """从质检问题推断「症状所在阶段」；推不出时返回空串。

    刻意**不**默认返回 "quality"：否则任何上游失败都会命中 R2（把症状当成质检），
    让 R4 与 LLM 兜底永远没有机会。
    """
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity", "")) != "error":
            continue
        stage = CATEGORY_TO_STAGE.get(str(issue.get("category", "")))
        if stage:
            return stage
    return ""


def _as_attr(
    *, stage: str, reason: str, confidence: str, source: str,
    unrepairable: bool = False, evidence: Sequence[str] = (),
    upstream: Sequence[str] = (),
) -> Attribution:
    return Attribution(
        primary_stage=stage,
        upstream_chain=list(upstream),
        evidence_refs=[e for e in evidence if e],
        confidence=confidence,
        unrepairable=unrepairable,
        reason=reason,
        source=source,
    )


def attribute(
    ledger_or_entries: Any,
    *,
    issues: Sequence[dict[str, Any]] | None = None,
) -> Attribution:
    """规则归因（确定性，不调用 LLM）。

    优先级：R1 资源缺失 → R3 上游申报 needs_upstream → R2 上游失败未消化 → R4 类别表。
    """
    entries = _entries_of(ledger_or_entries)
    if not entries:
        return _as_attr(stage="", reason="账本为空，无法归因", confidence="low", source="fallback")

    all_stages = {_get(e, "stage", "") for e in entries}
    failing_stages = {_get(e, "stage", "") for e in entries if _get(e, "failure") is not None}

    # ── R1：资源缺失（最强证据；重试无意义）────────────────────
    for entry in entries:
        for d in _get(entry, "degradations", []) or []:
            kind = str(_get(d, "kind", ""))
            if kind in RESOURCE_KINDS:
                stage = _get(entry, "stage", "")
                detail = str(_get(d, "detail", "") or kind)
                scope = str(_get(d, "scope", "") or "")
                return _as_attr(
                    stage=stage,
                    reason=f"{STAGE_CN.get(stage, stage)}阶段出现资源缺失：{detail}"
                           + (f"（{scope}）" if scope else ""),
                    confidence="high",
                    source="rule",
                    unrepairable=True,
                    evidence=[_get(entry, "id", "")],
                    upstream=_sorted_stages(s for s in all_stages if _stage_rank(s) < _stage_rank(stage)),
                )

    # ── R3：下游主动申报「需要上游补什么」─────────────────────
    for entry in entries:
        failure = _get(entry, "failure")
        if failure is None:
            continue
        if str(_get(failure, "retryable", "")) != "needs_upstream":
            continue
        needs = _get(failure, "needs", []) or []
        if not needs:
            continue
        want = needs[0]
        from_stage = str(_get(want, "from_stage", "") or "")
        field = str(_get(want, "field", "") or "")
        if not from_stage:
            continue
        return _as_attr(
            stage=from_stage,
            reason=f"{STAGE_CN.get(_get(entry, 'stage', ''), '')}阶段申报需要上游补齐"
                   + (f" {from_stage}.{field}" if field else f" {from_stage}"),
            confidence="mid",
            source="rule",
            evidence=[_get(entry, "id", "")],
            upstream=_sorted_stages(
                s for s in all_stages if _stage_rank(s) < _stage_rank(from_stage)
            ),
        )

    # ── R2：上游有失败、但管线继续走了（失败未被消化）─────────
    # 需要「症状所在阶段」作为参照；推不出症状就不硬判（交给 R4 / LLM 兜底）。
    symptom = _issue_stage(issues)
    upstream_failures = _sorted_stages(
        s for s in failing_stages if _stage_rank(s) < _stage_rank(symptom)
    ) if symptom else []
    if upstream_failures:
        culprit = upstream_failures[0]
        ref = next(
            (_get(e, "id", "") for e in entries
             if _get(e, "stage", "") == culprit and _get(e, "failure") is not None),
            "",
        )
        return _as_attr(
            stage=culprit,
            reason=f"{STAGE_CN.get(culprit, culprit)}阶段失败后管线继续执行，"
                   f"症状出现在{STAGE_CN.get(symptom, symptom)}",
            confidence="high",
            source="rule",
            evidence=[ref],
            upstream=[s for s in upstream_failures if s != culprit],
        )

    # ── R4：静态类别表（兜底，置信度最低）─────────────────────
    for issue in issues or []:
        if not isinstance(issue, dict) or str(issue.get("severity", "")) != "error":
            continue
        mapped = CATEGORY_TO_STAGE.get(str(issue.get("category", "")))
        if mapped:
            return _as_attr(
                stage=mapped,
                reason=f"按质检问题类别「{issue.get('category')}」映射到"
                       f"{STAGE_CN.get(mapped, mapped)}阶段（无账本证据，置信度低）",
                confidence="low",
                source="rule",
            )

    return _as_attr(
        stage="",
        reason="账本中没有可解释的失败/降级证据，未能定位责任阶段",
        confidence="low",
        source="rule",
    )


# ── LLM 兜底 ────────────────────────────────────────────────

_ATTRIBUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primary_stage": {
            "type": "string",
            "enum": list(STAGE_ORDER) + [""],
            "description": "主责任阶段；无法判断时为空字符串",
        },
        "confidence": {"type": "string", "enum": ["high", "mid", "low"]},
        "reason": {"type": "string", "description": "一句人话理由"},
        "unrepairable": {"type": "boolean"},
    },
    "required": ["primary_stage", "confidence", "reason"],
}


async def attribute_with_llm(
    ledger_or_entries: Any,
    *,
    issues: Sequence[dict[str, Any]] | None = None,
    timeout: float | None = None,
) -> Attribution:
    """规则归因 + LLM 兜底（仅在规则全不命中且有证据时调用）。"""
    base = attribute(ledger_or_entries, issues=issues)
    if base.primary_stage:
        return base

    entries = _entries_of(ledger_or_entries)
    has_evidence = bool(issues) or any(
        _get(e, "failure") is not None or _get(e, "degradations") for e in entries
    )
    if not has_evidence:
        return base

    from clipwright.config import settings

    ledger_text = _summarize(entries)
    issue_text = "\n".join(
        f"- [{i.get('severity')}] {i.get('category')}: {str(i.get('message', ''))[:200]}"
        for i in (issues or [])[:10]
        if isinstance(i, dict)
    )
    system = (
        "你是视频制作管线的故障归因器。只根据给定证据判断「责任在哪个阶段」，"
        "不要建议修复方式，不要引用外部知识。阶段顺序："
        + " → ".join(STAGE_ORDER)
        + "。若证据不足，primary_stage 输出空字符串、confidence 输出 low。"
    )
    user = f"## 账本\n{ledger_text}\n\n## 质检问题\n{issue_text or '（无）'}"

    try:
        import asyncio

        from clipwright.services.llm import LLMService

        svc = LLMService()
        data = await asyncio.wait_for(
            svc.structured_output(
                system_prompt=system,
                user_prompt=user,
                output_schema=_ATTRIBUTION_SCHEMA,
                use_flash=True,
                pipeline_id="",  # 兜底判定单独记账，不挂到某条管线
            ),
            timeout=timeout or settings.supervisor_attribution_timeout_sec,
        )
        stage = str((data or {}).get("primary_stage", "") or "")
        if stage not in STAGE_ORDER:
            return base.model_copy(update={"source": "fallback"})
        return Attribution(
            primary_stage=stage,
            upstream_chain=[],
            evidence_refs=[],
            confidence=str((data or {}).get("confidence", "low")),
            unrepairable=bool((data or {}).get("unrepairable", False)),
            reason=str((data or {}).get("reason", ""))[:300] or "由 LLM 兜底判定",
            source="llm",
        )
    except Exception as e:  # noqa: BLE001 — 兜底失败不得影响管线
        logger.warning("LLM 归因兜底失败（回退规则结论）: %s", e)
        return base.model_copy(update={"source": "fallback"})


def _summarize(entries: Sequence[Any], max_chars: int = 2500) -> str:
    lines: list[str] = []
    for e in entries:
        stage = _get(e, "stage", "")
        attempt = _get(e, "attempt", 0)
        decision = _get(e, "decision", "")
        lines.append(f"- {stage} 第{attempt}次 {decision}")
        failure = _get(e, "failure")
        if failure is not None:
            lines.append(
                "  失败: " + json.dumps(
                    {k: _get(failure, k) for k in ("category", "blocking_point", "retryable")},
                    ensure_ascii=False,
                )
            )
        for d in (_get(e, "degradations", []) or [])[:3]:
            lines.append(
                f"  降级: {_get(d, 'kind', '')} scope={_get(d, 'scope', '')} "
                f"{str(_get(d, 'detail', ''))[:120]}"
            )
    text = "\n".join(lines)
    return text[:max_chars]
