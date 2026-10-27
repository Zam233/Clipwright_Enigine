"""统一 Agent 运行时（PRD §2.4）：阶段子 agent 与调研 worker 走同一契约。

对外只暴露 `run(spec, task)` → `AgentResult`。内部按 `spec.kind` 选 adapter：

- `stage`  → `services.stage_subagent.run_stage`（阶段 Agent，写报告/产物/黑板）
- `research` → `supervisor.spawn.spawn_subagent`（只读调研员，带并发/审核门）

这样主 agent 只有一个工具（`delegate_agent` / `research_agent`）两种 spec，
不再需要"能力名解释器"（stage_capability 那种 if/elif）。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from clipwright.config import logger
from clipwright.schema.run import AgentResult, RunSpec

#: 进程内 run 表（统一视图：阶段 + 调研）
_RUNS: dict[str, AgentResult] = {}
_MAX_RUNS = 400
#: 幂等缓存：同一 idem_key 不重复干活（阶段/调研共用，不再各存一份）
_IDEM: dict[str, AgentResult] = {}
_MAX_IDEM = 200


def _record(result: AgentResult) -> None:
    if len(_RUNS) >= _MAX_RUNS:
        _RUNS.pop(next(iter(_RUNS)))
    _RUNS[result.run_id] = result


def replay(idem_key: str) -> AgentResult | None:
    cached = _IDEM.get(idem_key.strip())
    return cached.model_copy(deep=True) if cached else None


def get_run(run_id: str) -> dict[str, Any] | None:
    r = _RUNS.get(run_id)
    return r.model_dump(mode="json") if r else None


def list_runs(task_id: str = "") -> list[dict[str, Any]]:
    items = [r for r in _RUNS.values() if not task_id or r.run_id.startswith(task_id)]
    return [r.model_dump(mode="json") for r in items]


def inspect(*, run_id: str = "", task_id: str = "", stage: str = "") -> dict[str, Any] | None:
    """按 run_id 或（task_id + stage）查一次结果——两条查法都只是索引，不是分支逻辑。"""
    if run_id:
        hit = _RUNS.get(run_id)
        if hit is not None:
            return hit.model_dump(mode="json")
    if task_id and stage:
        from clipwright.services import stage_subagent

        legacy = stage_subagent.get_stage(task_id, stage)
        if legacy is not None:
            return legacy
    if task_id:
        cands = [r for r in _RUNS.values()
                 if r.stage == stage and r.status in ("ok", "degraded", "needs", "failed")]
        if cands:
            return cands[-1].model_dump(mode="json")
    return None


def cancel(run_id: str) -> tuple[bool, dict[str, Any]]:
    """取消一个还在跑的子 agent（阶段/调研共用）。"""
    if not run_id:
        return False, {"error": "需要 run_id"}
    try:
        from clipwright.supervisor.tracker import get_tracker

        tracker = get_tracker("")
        if tracker.cancel(run_id):
            return True, {"run_id": run_id, "status": "cancelled"}
    except Exception as exc:  # noqa: BLE001 — 取消失败要如实报
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    return False, {"error": f"子 agent 不可取消或已结束: {run_id}"}


def reset() -> None:
    _RUNS.clear()
    _IDEM.clear()


async def run(spec: RunSpec, task: Any) -> AgentResult:
    """执行一次子 agent（唯一入口）。"""
    run_id = spec.idem_key or f"run_{uuid.uuid4().hex[:10]}"
    started = time.monotonic()
    trace_id = spec.trace_id or str(getattr(task, "id", "") or "")

    # 幂等：同一 idem_key 直接复用上次结果（阶段/调研同一条规则，不再各写一份）
    if spec.idem_key:
        cached = replay(spec.idem_key)
        if cached is not None:
            return cached

    # 拓扑：星型，深度固定 1（子 agent 不得再派）
    if int(spec.depth or 1) > 1:
        result = AgentResult(
            run_id=run_id, kind=spec.kind, stage=spec.stage, role=spec.role,
            status="refused", error="子 agent 不得再派子 agent（星型拓扑，深度固定 1）",
            failure={"category": "policy_refused", "retryable": "permanent",
                     "blocking_point": "depth>1", "evidence": []},
        )
        _record(result)
        return result

    try:
        if spec.kind == "stage":
            result = await _run_stage(spec, task, run_id, trace_id)
        else:
            result = await _run_research(spec, task, run_id, trace_id)
    except Exception as exc:  # noqa: BLE001 — 派发异常也要结构化返回
        logger.warning("agent_run 失败 %s/%s: %s", spec.kind, spec.stage, exc)
        result = AgentResult(
            run_id=run_id, kind=spec.kind, stage=spec.stage, role=spec.role,
            status="failed", error=f"{type(exc).__name__}: {exc}",
            failure={"category": "internal_defect", "retryable": "needs_human",
                     "blocking_point": str(exc)[:200], "evidence": []},
        )
    result.duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    _record(result)
    _after_run(result, spec, task)
    if spec.idem_key and result.status in ("ok", "degraded"):
        if len(_IDEM) >= _MAX_IDEM:
            _IDEM.pop(next(iter(_IDEM)))
        _IDEM[spec.idem_key] = result.model_copy(deep=True)
    return result


def _after_run(result: AgentResult, spec: RunSpec, task: Any) -> None:
    """run 结束后的统一记账：指标 + 情景记忆（失败也不能跳过）。

    放在这里而不是各 adapter 里：阶段与调研必须用同一套口径，否则"成本条"
    与"失败率"永远对不上。
    """
    task_id = str(getattr(task, "id", "") or spec.trace_id)
    try:
        from clipwright.services import evaluation

        evaluation.record_run(
            task_id=task_id, run_id=result.run_id, kind=result.kind,
            status=result.status, tool_calls=len(result.tool_calls),
            duration_ms=result.duration_ms, cost_usd=result.cost_usd,
            provider=result.provider, model=result.model,
            extra={"stage": result.stage, "role": result.role,
                   "tokens": result.tokens,
                   "failure": (result.failure or {}).get("category")},
        )
    except Exception as exc:  # noqa: BLE001 — 记账失败不影响返回
        logger.warning("metrics 记账失败 %s: %s", result.run_id, exc)
    try:
        from clipwright.services import memory

        memory.remember(
            f"{result.kind}/{result.stage or result.role} → {result.status}："
            f"{result.summary or result.error or '（无结论）'}",
            task_id=task_id, layer="episodic",
            kind="outcome" if result.status in ("ok", "degraded") else "failure",
            provenance={"source": "agent_run", "run_id": result.run_id,
                        "ref": result.report_path or ""},
            tags=[result.kind, result.stage or result.role],
            pinned=result.status not in ("ok", "degraded"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("episodic 写入失败 %s: %s", result.run_id, exc)


async def _run_stage(spec: RunSpec, task: Any, run_id: str, trace_id: str) -> AgentResult:
    from clipwright.schema.stage import StageSpec
    from clipwright.services import stage_subagent

    stage_spec = StageSpec(
        stage=spec.stage,  # type: ignore[arg-type]
        args={"brief": spec.brief} if spec.brief else {},
        inputs=dict(spec.inputs or {}),
        provider=spec.provider,
        model=spec.model,
        timeout_sec=float(spec.timeout_sec or 900.0),
        max_turns=int(spec.max_turns or 8),
        idem_key=spec.idem_key,
    )
    sr = await stage_subagent.run_stage(task, stage_spec)
    failure = sr.failure.model_dump(mode="json") if sr.failure else None
    return AgentResult(
        run_id=run_id, kind="stage", stage=sr.stage, role=spec.role or sr.stage,
        status=_map_status(sr.status),
        summary=sr.summary, outputs=dict(sr.artifacts or {}), writes=list(sr.writes or []),
        report_path=sr.report_path, report_seq=sr.report_seq,
        issues=list(sr.issues or []),
        cost_usd=sr.cost_usd, cost_unknown=sr.cost_unknown,
        duration_ms=sr.duration_ms, provider=sr.provider, model=sr.model,
        failure=failure, error=sr.error,
    )


async def _run_research(spec: RunSpec, task: Any, run_id: str, trace_id: str) -> AgentResult:
    from types import SimpleNamespace

    from clipwright.supervisor.spawn import SpawnError, spawn_subagent

    ctx = SimpleNamespace(pipeline_id=trace_id or getattr(task, "id", ""), extra_params={})
    try:
        res = await spawn_subagent(
            ctx,
            role=spec.role or "调研员",
            system_prompt=spec.brief,
            tools=list(spec.tools or ["read_file", "list_dir"]),
            provider=spec.provider,
            model=spec.model,
            timeout_sec=float(spec.timeout_sec or 60.0),
            max_turns=int(spec.max_turns or 5),
        )
    except SpawnError as exc:
        return AgentResult(
            run_id=run_id, kind="research", role=spec.role, status="refused",
            error=str(exc),
            failure={"category": "spawn_rejected", "retryable": "needs_human",
                     "blocking_point": str(exc)[:200], "evidence": []},
        )
    raw = str(res.get("status") or "")
    review = dict(res.get("review") or {})
    if raw == "pending_review":
        return AgentResult(
            run_id=run_id, kind="research", role=spec.role, status="needs",
            summary="等待人工审核（带执行权限的子代理必须先过审核门）",
            outputs={"review": review, "worker_id": res.get("worker_id")},
            tokens=int(res.get("tokens") or 0),
            tool_calls=list(res.get("tool_calls") or []),
            cost_usd=res.get("cost_usd"), cost_unknown=res.get("cost_usd") is None,
            duration_ms=int(res.get("duration_ms") or 0),
            failure={"category": "needs_review", "retryable": "needs_human",
                     "blocking_point": "等待人工审核", "evidence": [],
                     "review": review},
        )
    status = {"done": "ok", "failed": "failed"}.get(raw, "failed")
    return AgentResult(
        run_id=run_id, kind="research", role=spec.role, status=status,  # type: ignore[arg-type]
        summary=str(res.get("output") or "")[:800],
        outputs={"conclusion": str(res.get("output") or "")[:2000],
                 "subagent_id": str(res.get("subagent_id") or "")},
        tool_calls=list(res.get("tool_calls") or []),
        tokens=int(res.get("tokens") or 0),
        cost_usd=res.get("cost_usd"), cost_unknown=res.get("cost_usd") is None,
        duration_ms=int(res.get("duration_ms") or 0),
        failure=res.get("failure"),
        error=str(res.get("error") or ""),
    )


def _map_status(stage_status: str) -> Any:
    return {
        "ok": "ok",
        "degraded": "degraded",
        "failed": "failed",
        "refused": "refused",
        "cancelled": "cancelled",
        "dry_run": "ok",
    }.get(str(stage_status), "failed")


__all__ = ["cancel", "get_run", "inspect", "list_runs", "replay", "reset", "run"]
