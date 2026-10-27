"""统一 Agent 运行时（PRD §2.4）：阶段子 agent 与调研 worker 走同一契约。

对外只暴露 `run(spec, task)` → `AgentResult`。内部按 `spec.kind` 选 adapter：

- `stage`  → `services.stage_subagent.run_stage`（阶段 Agent，写报告/产物/黑板）
- `research` → `supervisor.spawn.spawn_subagent`（只读调研员，带并发/审核门）

这样主 agent 只有一个工具（`delegate_agent` / `research_agent`）两种 spec，
不再需要"能力名解释器"（那种 if/elif 已随 closeout B11 退役）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from clipwright.config import logger
from clipwright.schema.run import AgentResult, RunSpec

#: Kernel 可直接授予子 agent 的只读/低危工具（与 supervisor.SUPERVISOR_TOOLS 的交集）。
#: 这条策略原来在 `subagent_capability` 里；适配层退役后它必须留在**运行时**——
#: 否则"子 agent 能不能开 shell"就没人管了。
READONLY_TOOLS: frozenset[str] = frozenset({
    # PRD §17.5.2：子 agent 不得写文件（报告由 runtime 落盘）
    "read_file", "list_dir", "list_subagents", "get_subagent_status",
    "web_search", "web_fetch",
})
EXEC_TOOLS: frozenset[str] = frozenset({"run_shell", "run_python"})


class RunRefused(Exception):
    """派发前的策略拒绝（调用方转成 AgentResult(status="refused")）。"""

    def __init__(self, message: str, *, category: str = "policy_refused") -> None:
        super().__init__(message)
        self.category = category


def enforce_tool_policy(spec: RunSpec) -> list[str]:
    """调研 worker 的工具策略：只读子集，禁止 shell/python。

    阶段子 agent 用自身能力集，不走这条（它们有自己的工具白名单）。
    """
    requested = {str(t) for t in (spec.tools or ["read_file", "list_dir"])}
    forbidden = requested & EXEC_TOOLS
    if forbidden:
        raise RunRefused(
            f"子 agent 不能由 Kernel 直接授予执行权限（{sorted(forbidden)}）："
            "带 shell/python 的子 agent 必须经 supervisor 派发，"
            "由独立风险判定 + 人工审核把关"
        )
    allowed = sorted(requested & READONLY_TOOLS)
    if not allowed:
        raise RunRefused("子 agent 工具集必须是非空的只读子集")
    return allowed


#: 进程内 run 表（统一视图：阶段 + 调研）
_RUNS: dict[str, AgentResult] = {}
_MAX_RUNS = 400
#: 幂等缓存：同一 idem_key 不重复干活（阶段/调研共用，不再各存一份）
_IDEM: dict[str, AgentResult] = {}
_MAX_IDEM = 200
#: **运行中的 run → 取消句柄**（这是取消契约的唯一事实来源）
#:
#: 为什么不用 supervisor tracker：tracker 按 `subagent_id` 建键，阶段子 agent 根本
#: 不注册进去（它直接跑 `stage_subagent.run_stage`）。用 run_id 去查那个表只会永远
#: 返回"不可取消"——取消契约看着有、实际是空的。
_LIVE: dict[str, dict[str, Any]] = {}
#: 别名索引：subagent_id / idem_key / stage 名 → run_id（让旧调用方也能取消）
_ALIAS: dict[str, str] = {}
_MAX_ALIAS = 800


def _bind_alias(alias: str, run_id: str) -> None:
    alias = (alias or "").strip()
    if not alias or alias == run_id:
        return
    if len(_ALIAS) >= _MAX_ALIAS:
        _ALIAS.pop(next(iter(_ALIAS)))
    _ALIAS[alias] = run_id


def _resolve_run_id(run_id: str) -> str:
    key = (run_id or "").strip()
    return _ALIAS.get(key, key)


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
    """按任务列 run。

    早期版本用 `run_id.startswith(task_id)` 过滤——但 run_id 是随机/幂等键，
    带前缀只是巧合。`AgentResult.task_id` 才是真字段。
    """
    key = str(task_id or "")
    items = [r for r in _RUNS.values()
             if not key or r.task_id == key or r.run_id.startswith(key)]
    return [r.model_dump(mode="json") for r in items]


def _tracker_node(task_id: str, node_id: str) -> dict[str, Any] | None:
    """回落到 supervisor worker 表查一次（历史 worker 与它的 tracker 是另一套存储）。"""
    try:
        from clipwright.supervisor.tracker import get_tracker

        for pid in [task_id, ""]:
            node = get_tracker(pid).get(str(node_id))
            if node is not None:
                return {"run_id": node.id, "kind": "research", "role": node.role,
                        "status": node.status, "summary": (node.last_output or "")[:500],
                        "cost_usd": node.cost_usd, "duration_ms": node.duration_ms,
                        "source": "tracker"}
    except Exception:  # noqa: BLE001 — tracker 不可用就当没查到
        return None
    return None


def inspect(*, run_id: str = "", task_id: str = "", stage: str = "") -> dict[str, Any] | None:
    """按 run_id 或（task_id + stage）查一次结果——两条查法都只是索引，不是分支逻辑。"""
    if run_id:
        hit = _RUNS.get(run_id)
        if hit is not None:
            return hit.model_dump(mode="json")
        found = _tracker_node(task_id, run_id)
        if found is not None:
            return found
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


def cancel(run_id: str, *, task_id: str = "") -> tuple[bool, dict[str, Any]]:
    """取消一个还在跑的子 agent（阶段/调研共用）。

    三级查找：本服务的 `_LIVE` 句柄表 → 别名索引（subagent_id / idem_key）→
    supervisor tracker（历史 worker）。前两级是**唯一可靠**的路径：阶段 run 不在
    tracker 里，调研 run 的 id 也与 tracker 的键不同。
    """
    key = (run_id or "").strip()
    if not key:
        return False, {"error": "需要 run_id"}
    resolved = _resolve_run_id(key)

    live = _LIVE.get(resolved) or _LIVE.get(key)
    if live is not None:
        handle = live.get("task")
        try:
            if handle is not None and not handle.done():
                handle.cancel()
        except Exception as exc:  # noqa: BLE001 — 取消失败要如实报
            return False, {"error": f"{type(exc).__name__}: {exc}"}
        live["cancelled"] = True
        return True, {"run_id": resolved, "status": "cancelled",
                      "kind": live.get("kind", "")}

    try:
        from clipwright.supervisor.tracker import get_tracker

        # worker tracker 是按 pipeline/task 分实例的：先查本任务的那张，再兜底全局
        for pid in ([str(task_id)] if task_id else []) + [""]:
            tracker = get_tracker(pid)
            if tracker.cancel(key) or tracker.cancel(resolved):
                return True, {"run_id": resolved, "status": "cancelled",
                              "via": "tracker", "task_id": pid}
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracker 取消失败 %s: %s", key, exc)

    done = _RUNS.get(resolved)
    if done is not None:
        return False, {"error": f"run 已结束（{done.status}），不可取消",
                       "run_id": resolved}
    return False, {"error": f"子 agent 不可取消或不存在: {key}"}


def reset() -> None:
    for live in list(_LIVE.values()):
        task = live.get("task")
        try:
            if task is not None and not task.done():
                task.cancel()
        except Exception:  # noqa: BLE001
            pass
    _LIVE.clear()
    _ALIAS.clear()
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
            _emit_run(task, "agent_run_update",
                      f"{spec.kind}/{spec.stage or spec.role} → 复用上次结果",
                      detail={"run_id": cached.run_id, "status": cached.status,
                              "replayed": True, "idem_key": spec.idem_key},
                      run_id=cached.run_id, span="agent", trace_id=trace_id)
            return cached

    _emit_run(task, "agent_run_start", f"派出 {spec.kind}/{spec.stage or spec.role}",
              detail={"run_id": run_id, "kind": spec.kind, "stage": spec.stage,
                      "role": spec.role, "brief": spec.brief[:200],
                      "inputs": list((spec.inputs or {}).keys())},
              run_id=run_id, span="agent", trace_id=trace_id)

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
        # 把真正的执行放进 asyncio.Task 并登记句柄：取消要有东西可取消。
        # 旧实现只把 run_id 交给 supervisor tracker，而阶段 run 不在那张表里
        # ——"取消契约"于是写着有、实际永远返回不可取消。
        handle = asyncio.ensure_future(_dispatch(spec, task, run_id, trace_id))
        _LIVE[run_id] = {"task": handle, "kind": spec.kind,
                         "stage": spec.stage, "role": spec.role}
        _bind_alias(spec.idem_key, run_id)
        try:
            result = await handle
        except asyncio.CancelledError:
            result = AgentResult(
                run_id=run_id, kind=spec.kind, stage=spec.stage, role=spec.role,
                status="cancelled", error="子 agent 已被取消",
                failure={"category": "cancelled", "retryable": "permanent",
                         "blocking_point": "人工取消", "evidence": []},
            )
        finally:
            _LIVE.pop(run_id, None)
        # 上游真实 subagent_id 也登记成别名，旧的 cancel_subagent(subagent_id=…) 才能取消
        if result.outputs.get("subagent_id"):
            _bind_alias(str(result.outputs["subagent_id"]), run_id)
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
    if result.status == "needs":
        # 等人工审核：既不是成功也不是失败，用 update 事件让前端显示"卡在哪"
        review = (result.failure or {}).get("review") or {}
        _emit_run(task, "agent_run_update",
                  f"{spec.kind}/{result.stage or result.role} → 等待人工审核",
                  detail={"run_id": result.run_id, "status": "needs_review",
                          "risk": review.get("risk"),
                          "reasons": review.get("reasons") or []},
                  run_id=result.run_id, span="agent", trace_id=trace_id)
    _after_run(result, spec, task)
    _emit_run(task, "agent_run_end",
              f"{spec.kind}/{result.stage or result.role} → {result.status}",
              detail={"run_id": result.run_id, "status": result.status,
                      "summary": result.summary[:300], "cost_usd": result.cost_usd,
                      "report_path": result.report_path,
                      "failure": (result.failure or {}).get("category")},
              run_id=result.run_id, span="agent", trace_id=trace_id)
    if spec.idem_key and result.status in ("ok", "degraded"):
        if len(_IDEM) >= _MAX_IDEM:
            _IDEM.pop(next(iter(_IDEM)))
        _IDEM[spec.idem_key] = result.model_copy(deep=True)
    return result


async def _dispatch(spec: RunSpec, task: Any, run_id: str,
                    trace_id: str) -> AgentResult:
    if spec.kind == "stage":
        return await _run_stage(spec, task, run_id, trace_id)
    return await _run_research(spec, task, run_id, trace_id)


def _emit_run(task: Any, event_type: str, message: str, *, detail: dict[str, Any],
              run_id: str, span: str, trace_id: str) -> None:
    """统一的 run 事件（阶段与调研同一组 `agent_run_*`，前端只需认这一种）。"""
    try:
        from clipwright.services.task_events import emit

        emit(str(getattr(task, "id", "") or trace_id), event_type, message, detail,
             trace_id=trace_id, run_id=run_id, span=span)
    except Exception as exc:  # noqa: BLE001 — 事件写失败不能带崩派发
        logger.warning("agent_run 事件写入失败 %s: %s", event_type, exc)


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
        from clipwright.services import circuit_breaker

        key = result.stage or result.role or result.kind
        if result.status in ("ok", "degraded"):
            circuit_breaker.record_success(key)
        elif result.status in ("failed", "refused"):
            circuit_breaker.record_failure(key)
    except Exception as exc:  # noqa: BLE001 — 熔断记账失败不影响返回
        logger.warning("熔断记账失败 %s: %s", result.run_id, exc)
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
    from clipwright.schema.stage import STAGE_NAMES, StageSpec
    from clipwright.services import stage_subagent

    # 阶段名校验：这是**运行时**的输入契约（旧适配层退役后必须留在这里）
    if not spec.stage:
        return AgentResult(
            run_id=run_id, kind="stage", task_id=str(getattr(task, "id", "") or ""),
            trace_id=trace_id, status="refused",
            error="派阶段必须给 stage（structure/material/edit/animation/audio/quality）",
            failure={"category": "input_insufficient", "retryable": "needs_human",
                     "blocking_point": "缺少 stage 参数", "evidence": []},
        )
    if str(spec.stage) not in STAGE_NAMES:
        return AgentResult(
            run_id=run_id, kind="stage", task_id=str(getattr(task, "id", "") or ""),
            trace_id=trace_id, stage=str(spec.stage), status="refused",
            error=f"未知阶段 {spec.stage}（可选：{'/'.join(STAGE_NAMES)}）",
            failure={"category": "input_insufficient", "retryable": "needs_human",
                     "blocking_point": f"stage={spec.stage}", "evidence": []},
        )

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
        run_id=run_id, kind="stage",
        task_id=str(getattr(task, "id", "") or ""), trace_id=trace_id,
        stage=sr.stage, role=spec.role or sr.stage,
        status=_map_status(sr.status),
        summary=sr.summary, outputs=dict(sr.artifacts or {}), writes=list(sr.writes or []),
        report_path=sr.report_path, report_seq=sr.report_seq,
        issues=list(sr.issues or []),
        # 归因（PRD §2.4.2）：阶段侧的 token 与工具轨迹由 StageResult 带上来；
        # 拿不到就保持 0/空 + cost_unknown，不伪造数字。
        tokens=int(getattr(sr, "tokens", 0) or 0),
        tool_calls=list(getattr(sr, "tool_calls", None) or []),
        cost_usd=sr.cost_usd, cost_unknown=sr.cost_unknown,
        duration_ms=sr.duration_ms, provider=sr.provider, model=sr.model,
        failure=failure, error=sr.error,
    )


async def _run_research(spec: RunSpec, task: Any, run_id: str, trace_id: str) -> AgentResult:
    from types import SimpleNamespace

    from clipwright.supervisor.spawn import SpawnError, spawn_subagent

    try:
        allowed_tools = enforce_tool_policy(spec)
    except RunRefused as exc:
        return AgentResult(
            run_id=run_id, kind="research", role=spec.role, status="refused",
            error=str(exc),
            failure={"category": exc.category, "retryable": "needs_human",
                     "blocking_point": str(exc)[:200], "evidence": []},
        )

    ctx = SimpleNamespace(pipeline_id=trace_id or getattr(task, "id", ""), extra_params={})
    try:
        res = await spawn_subagent(
            ctx,
            role=spec.role or "调研员",
            system_prompt=spec.brief,
            tools=allowed_tools,
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
            run_id=run_id, kind="research",
            task_id=str(getattr(task, "id", "") or ""), trace_id=trace_id,
            role=spec.role, status="needs",
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
        run_id=run_id, kind="research",
        task_id=str(getattr(task, "id", "") or ""), trace_id=trace_id,
        role=spec.role, status=status,  # type: ignore[arg-type]
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


__all__ = ["EXEC_TOOLS", "READONLY_TOOLS", "RunRefused", "cancel",
           "enforce_tool_policy", "get_run", "inspect", "list_runs", "replay",
           "reset", "run"]
