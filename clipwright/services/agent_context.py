"""子 agent 运行上下文（现代 agent 化：子 agent 的每一步都归属到某个 Task）。

为什么需要它：
主 agent 与子 agent 是**同一条事件流上的两个会话**（DeepSeek harness / opencode /
Claude Code 的 subagent 语义）——子 agent 内部调了什么工具、花了多久、失败几次，
必须实时可见并归属到发起它的任务。但 `BaseAgent.call_tool` 拿不到 task_id
（Agent 是纯业务对象，不持有编排层状态）。

做法：编排层在跑阶段前用 `task_scope(task_id)` 建立作用域，工具调用点通过
`current_task_id()` 读回来发事件。没有作用域时（单测/脚本直调 Agent）静默跳过，
不影响任何既有行为。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

#: 当前任务 id（阶段子 agent 的每次工具调用都归到它）
_TASK_ID: ContextVar[str] = ContextVar("agent_task_id", default="")
#: 当前阶段名（事件里标明是哪一步在干活）
_STAGE: ContextVar[str] = ContextVar("agent_stage", default="")
#: 事件节流：每个阶段最多上报多少条工具事件（超出只记账不发事件）
_TOOL_EVENT_BUDGET: ContextVar[int] = ContextVar("agent_tool_event_budget", default=400)
#: 内部操作轨迹（PRD modern-agent-review M2「暗区补齐」）：
#: 素材源检索 / MG 生成 / 插件与 skill 调用不走 `call_tool`，但同样是"子 agent 干了什么"。
#: 用 `operation()` 记进来，`run_stage` 收尾并入 `StageResult.tool_calls`（source=internal）。
_OPERATIONS: ContextVar[list[dict] | None] = ContextVar("agent_operations", default=None)


def current_task_id() -> str:
    return _TASK_ID.get()


def current_stage() -> str:
    return _STAGE.get()


@contextmanager
def task_scope(task_id: str, stage: str = "", event_budget: int = 400) -> Iterator[None]:
    """把后续的工具调用/事件归到 `task_id`（可嵌套，退出时恢复）。"""
    t1 = _TASK_ID.set(str(task_id or ""))
    t2 = _STAGE.set(str(stage or ""))
    _prev_ops = _OPERATIONS.get()
    _OPERATIONS.set([])
    t3 = _TOOL_EVENT_BUDGET.set(int(event_budget))
    try:
        yield
    finally:
        _TOOL_EVENT_BUDGET.reset(t3)
        _OPERATIONS.set(_prev_ops)
        _STAGE.reset(t2)
        _TASK_ID.reset(t1)


def take_event_budget(n: int = 1) -> bool:
    """原子地消耗事件预算（True = 还能发）。"""
    left = _TOOL_EVENT_BUDGET.get()
    if left <= 0:
        return False
    _TOOL_EVENT_BUDGET.set(left - n)
    return True


def record_operation(
    name: str, *, status: str = "ok", duration_ms: int = 0,
    detail: str = "", source: str = "internal",
) -> dict:
    """记录一次**非工具**的外部动作（素材源检索 / MG 生成 / 插件·skill 调用）。

    这些动作不走 `call_tool`，以前只留 logger —— 主 agent 的轨迹里看不见。
    现在它们进当前作用域的操作表，`run_stage` 收尾时并入工具轨迹并落盘。
    """
    entry = {
        "tool": name,
        "capability": name,
        "status": str(status or "ok"),
        "duration_ms": int(duration_ms or 0),
        "args_brief": "",
        "output_brief": str(detail or "")[:120],
        "error": "" if str(status or "ok").lower() in ("ok", "success") else str(detail or "")[:200],
        "source": source,
    }
    ops = _OPERATIONS.get()
    if ops is not None:
        ops.append(entry)
        if len(ops) > 500:
            del ops[: len(ops) - 500]
    return entry


@contextmanager
def operation(name: str, *, source: str = "internal") -> Iterator[dict]:
    """给一段"非工具"动作计时并自动记账（异常也记 failed）。"""
    import time as _time

    started = _time.monotonic()
    box: dict = {"status": "ok", "detail": ""}
    try:
        yield box
    except Exception as exc:  # noqa: BLE001 — 记账后原样抛出
        box["status"] = "error"
        box["detail"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record_operation(
            name, status=str(box.get("status") or "ok"),
            duration_ms=int((_time.monotonic() - started) * 1000),
            detail=str(box.get("detail") or ""), source=source,
        )


def take_operations() -> list[dict]:
    """取走当前作用域的操作记录（run_stage 收尾用）。

    注意：**作用域外调用不写回**（保持 None 而不是设成空 list）——否则会把
    "没有作用域"变成"有一个空作用域"，后续 `record_operation` 就会跨阶段/跨测试累积。
    """
    ops = _OPERATIONS.get()
    if ops is None:
        return []
    _OPERATIONS.set([])
    return list(ops)


def emit_progress(message: str, detail: dict | None = None) -> bool:
    """子 agent 上报一条进度事件（无 task_scope / 预算耗尽时静默返回 False）。

    现代 agent 化（用户 2026-10-29）：长阶段（material 15 分钟、animation 29 段 MG）
    不能让主 agent 干等到最后一句 summary——每一步都该在事件流里看得见。
    """
    task_id = current_task_id()
    if not task_id or not take_event_budget():
        return False
    try:
        from clipwright.services.task_events import emit

        payload = {"stage": current_stage()}
        payload.update(detail or {})
        emit(task_id, "subagent_progress", str(message)[:200], payload, span="subagent")
        return True
    except Exception:  # noqa: BLE001 — 进度上报失败绝不能影响业务
        return False


__all__ = [
    "current_stage", "current_task_id", "emit_progress", "task_scope", "take_event_budget",
]
