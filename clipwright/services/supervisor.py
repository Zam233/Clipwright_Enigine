"""调度中枢：策略选择（PRD F3 / 开发计划 B3）。

职责刻意收窄：**只决定「用哪一招、派给谁」**，不执行、不碰 timeline。
执行走 `pipeline_v2.execute_order`（复用既有 `_dispatch`），审计走账本。

策略库固定 6 种（PRD F3，不开放自由生成）：
- S1_same            同参重做（首次失败、疑似偶发）
- S2_model           换模型重做（同阶段同模型已失败；B4 才真正生效）
- S3_upstream        上游联动重做（归因指向更上游）
- S4_accept_degraded 接受降级并标注（不可修复但可交付）
- S5_new_path        换实现路径（同阶段两次无效）
- S6_abort           止损上报（预算/轮次耗尽，或已无新招）

**硬规则（PRD G2）**：同一策略对同一阶段不得连续使用超过 1 次——命中即沿阶梯升级，
升级到顶仍重复则 S6。
"""

from __future__ import annotations

from typing import Iterable, Sequence
from uuid import uuid4

from clipwright.schema.supervisor import Attribution, SupervisorDecision

STRATEGIES: tuple[str, ...] = (
    "S1_same",
    "S2_model",
    "S3_upstream",
    "S4_accept_degraded",
    "S5_new_path",
    "S6_abort",
)

#: 逐级升级阶梯（S3 不在阶梯上：它属于「换目标」而不是「加力度」）
_ESCALATION: tuple[str, ...] = ("S1_same", "S2_model", "S5_new_path", "S6_abort")

STRATEGY_CN: dict[str, str] = {
    "S1_same": "同参重做",
    "S2_model": "换模型重做",
    "S3_upstream": "上游联动重做",
    "S4_accept_degraded": "接受降级",
    "S5_new_path": "换实现路径",
    "S6_abort": "止损上报",
}


def choose_strategy(
    attribution: Attribution | None,
    *,
    stage_attempts: dict[str, int] | None = None,
    used_pairs: Iterable[tuple[str, str]] = (),
    model_swaps: int = 0,
    max_model_swaps: int = 2,
    budget_exhausted: bool = False,
    mode: str = "shadow",
) -> SupervisorDecision:
    """选一个策略并给出理由（纯函数，无副作用，便于回归）。"""
    attempts = dict(stage_attempts or {})
    used = {(str(s), str(t)) for s, t in used_pairs}
    stage = attribution.primary_stage if attribution else ""

    def _decision(strategy: str, reason: str, *, target: str = "",
                  allow_degrade: bool = False, aborted: bool = False) -> SupervisorDecision:
        previous = next((s for st, s in used if st == target), "")
        return SupervisorDecision(
            id=f"dec_{uuid4().hex[:8]}",
            strategy=strategy,
            target_stage=target,
            reason=reason,
            allow_degrade=allow_degrade,
            aborted=aborted,
            stage_attempts=attempts,
            previous_strategy=previous,
            mode=mode,
        )

    if budget_exhausted:
        return _decision("S6_abort", "预算已耗尽，停止重试", aborted=True)

    if attribution is None or not stage:
        return _decision(
            "S6_abort",
            "证据不足以定位责任阶段，不再盲目重试，转人工",
            aborted=True,
        )

    if attribution.unrepairable:
        return _decision(
            "S4_accept_degraded",
            f"{attribution.reason}；重试同一做法不会变好，建议接受降级并标注",
            allow_degrade=True,
        )

    n = int(attempts.get(stage, 0))
    # ── 基础策略 ──
    if n <= 1:
        if attribution.upstream_chain and attribution.source == "rule":
            base = "S3_upstream"
            reason = f"{stage} 的问题可追溯到更上游的 {attribution.upstream_chain[0]}"
            target = attribution.upstream_chain[0]
        else:
            base = "S1_same"
            reason = f"{stage} 首次失败，先按同参重做一次确认是否偶发"
            target = stage
    elif n == 2:
        if max_model_swaps > 0 and model_swaps < max_model_swaps:
            base, reason, target = (
                "S2_model",
                f"{stage} 同模型已失败两次，换一个模型再试",
                stage,
            )
        else:
            base, reason, target = (
                "S5_new_path",
                f"{stage} 同模型已失败两次且无可换模型，换实现路径",
                stage,
            )
    else:
        base = "S5_new_path"
        reason = f"{stage} 已尝试 {n} 次，换实现路径（换素材源/换分镜策略/缩短段落）"
        target = stage

    # ── 硬规则：同阶段同策略不得连续用第二次 → 沿阶梯升级 ──
    final, final_target, note = _escalate(
        base, target, used, symptom_stage=stage,
        model_swaps=model_swaps, max_model_swaps=max_model_swaps,
    )
    if final == "S6_abort":
        return _decision(
            "S6_abort",
            f"{reason}；但 {STRATEGY_CN.get(base, base)} 已用过，可用的招已用尽 → 转人工",
            aborted=True,
        )
    if note:
        reason = f"{reason}；{note}"
    return _decision(final, reason, target=final_target)


def _escalate(
    base: str,
    target: str,
    used: set[tuple[str, str]],
    *,
    symptom_stage: str = "",
    model_swaps: int,
    max_model_swaps: int,
) -> tuple[str, str, str]:
    """命中「同阶段同策略已用过」时升级；S3 用过则回落到症状阶段。"""
    if base == "S3_upstream":
        if (target, base) not in used:
            return base, target, ""
        # 上游联动已做过 → 回到症状阶段换模型/换路径
        fallback_stage = symptom_stage or target
        base = "S2_model" if model_swaps < max_model_swaps else "S5_new_path"
        target = fallback_stage
        note = "上游联动已做过，改在症状阶段动手"
    else:
        note = ""

    idx = _ESCALATION.index(base) if base in _ESCALATION else 0
    while idx < len(_ESCALATION):
        candidate = _ESCALATION[idx]
        if candidate == "S2_model" and model_swaps >= max_model_swaps:
            idx += 1
            continue
        if (target, candidate) in used:
            idx += 1
            continue
        if candidate != base:
            note = (note + "；" if note else "") + f"{STRATEGY_CN.get(candidate, candidate)} 未试过，升级"
        return candidate, target, note
    return "S6_abort", "", note


def record_decision(state, decision: SupervisorDecision, *, pid: str = "") -> None:
    """把决策写进 state.shared_data（供 API/诊断书/前端读取），并留 trace。"""
    try:
        history = state.shared_data.get("decisions")
        if not isinstance(history, list):
            history = []
        history.append(decision.model_dump(mode="json"))
        # 只保留最近 50 条，避免长管线无限增长
        state.shared_data["decisions"] = history[-50:]
        if pid:
            from clipwright.services.trace import add_event

            add_event(
                pid, "supervisor", "dispatch",
                f"决策 {decision.strategy}"
                + (f" → {decision.target_stage}" if decision.target_stage else ""),
                decision.model_dump(mode="json"),
            )
    except Exception:  # noqa: BLE001 — 留痕失败不得影响决策执行
        pass
