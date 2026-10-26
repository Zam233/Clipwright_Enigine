"""阶段依赖与自愈闭包（PRD §3.7）。

**为什么要有独立的一份**：依赖关系原来只存在于 `pipeline_v2.AgentDAG._DEPS` 里，
而 `pipeline_v2` 在 P4 会被删除。主 Agent 要能回答"edit 重做之后该连带重做谁"，
这件事不能靠 LLM 记性——必须由代码保证。

语义与 `pipeline_v2._downstream_closure` 一致（含当年那个真实故障的教训）：
`_get_downstream_agents` 只返回直接依赖者时，重做 `edit` 只联动 `animation`，
`audio` 不重做 → 先前铺好的旁白/BGM/字幕随 edit 重建时间线静默丢失，成片无声交付。
所以这里做**传递闭包**，并且 quality 永远不进闭包（它是裁判，不是被重做的环节）。
"""

from __future__ import annotations

from typing import Iterable

#: 阶段依赖（与 `pipeline_v2.AgentDAG._DEPS` 同源；P4 删管线后这里是唯一真值）
STAGE_DEPS: dict[str, tuple[str, ...]] = {
    "structure": (),
    "material": ("structure",),
    "edit": ("structure", "material"),
    "animation": ("edit",),
    "audio": ("animation",),
    "quality": ("animation", "audio"),
}

#: 执行顺序（拓扑序，稳定的常量而不是算出来的，便于日志与断言对齐）
EXECUTION_ORDER: tuple[str, ...] = (
    "structure", "material", "edit", "animation", "audio", "quality",
)


def downstream_of(stage: str) -> list[str]:
    """直接依赖 `stage` 的阶段（一层）。"""
    return [a for a, deps in STAGE_DEPS.items() if stage in deps]


def closure(stage: str) -> list[str]:
    """需要连带重做的下游**传递闭包**（按执行序返回，不含 quality）。"""
    rev: dict[str, set[str]] = {a: set() for a in STAGE_DEPS}
    for dep, bases in STAGE_DEPS.items():
        for base in bases:
            rev.setdefault(base, set()).add(dep)

    closed: set[str] = set()
    frontier = list(rev.get(stage, set()))
    while frontier:
        cur = frontier.pop()
        if cur == "quality" or cur in closed:
            continue
        closed.add(cur)
        frontier.extend(rev.get(cur, set()))
    return [a for a in EXECUTION_ORDER if a in closed]


def replan_plan(redo_agent: str) -> list[str]:
    """质检建议重做时，主 Agent 应该派发的阶段序列（含被重做的那个）。"""
    stage = str(redo_agent or "").strip()
    if stage not in STAGE_DEPS:
        return []
    return [stage] + closure(stage)


def validate_plan(stages: Iterable[str]) -> list[str]:
    """校验一串阶段是否满足依赖（缺前置则返回缺失前置，空列表=合法）。"""
    have: set[str] = set()
    missing: list[str] = []
    for stage in stages:
        for dep in STAGE_DEPS.get(str(stage), ()):
            if dep not in have and dep not in missing:
                missing.append(dep)
        have.add(str(stage))
    return missing


# ── 能力层入口（主 Agent 可调）─────────────────────────────


async def run(task, step) -> dict:  # noqa: ANN001 — 与其它 capability 同签名
    """`replan_closure` / `validate_stage_plan` 能力。"""
    from clipwright.config import settings

    if str(getattr(settings, "agent_mode", "off") or "off").lower() != "on":
        return {
            "status": "refused",
            "error": "agent_mode != on：自愈闭包仅在主 Agent 模式下可用",
            "failure": {"category": "policy_refused", "retryable": "needs_human",
                        "blocking_point": "agent_mode != on", "evidence": []},
        }
    args = dict(getattr(step, "args", {}) or {})
    capability = str(getattr(step, "capability", ""))

    if capability == "validate_stage_plan":
        stages = [str(s) for s in (args.get("stages") or [])]
        if not stages:
            return {"status": "refused", "error": "validate_stage_plan 需要 stages 列表"}
        missing = validate_plan(stages)
        return {
            "status": "ok",
            "output": {"stages": stages, "missing_upstream": missing,
                       "ok": not missing},
        }

    redo = str(args.get("redo_agent") or "").strip()
    if not redo:
        return {
            "status": "refused",
            "error": "replan_closure 需要 redo_agent（质检给出的建议重做阶段）",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": "缺少 redo_agent", "evidence": []},
        }
    if redo not in STAGE_DEPS:
        return {
            "status": "refused",
            "error": f"未知阶段 {redo}（可选：{'/'.join(EXECUTION_ORDER)}）",
        }
    plan = replan_plan(redo)
    return {
        "status": "ok",
        "output": {
            "redo_agent": redo,
            "stages": plan,
            "reason": ("重做一个阶段必须连带重做它的下游：否则下游产物会基于旧时间线，"
                       "例如重做 edit 而不重做 audio，会让先前的配音/字幕静默丢失"),
        },
    }


__all__ = [
    "EXECUTION_ORDER",
    "STAGE_DEPS",
    "closure",
    "downstream_of",
    "replan_plan",
    "run",
    "validate_plan",
]
