"""Agent Kernel（路线 C / C2）：plan → act → observe → reflect。

它只做三件事：**想、选、记**。不直接碰副作用——所有执行经 `tool.surface.execute()`
（进而受档位与网关约束），这条不因为"kernel 很聪明"而放松。

四道闸（PRD F5 / Q1 已裁定，数值可调但不可置空）：
`max_turns=30` / `max_tool_calls=80` / `max_wall_sec=3600` / 金额闸（未配置=无上限）。
触闸**不是失败**，而是收尾：必须产出「结论 + 已完成 + 未完成原因」三件套（A1）。

续跑语义（F8/A6）：每个 Step 结束落一次检查点；`resume()` 从检查点继续，
已完成的 Step 不重跑——幂等键 `task:step:attempt` 让"重跑"退化为"取缓存结果"。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from clipwright.config import logger, settings
from clipwright.schema.task import (
    Plan,
    PlanStep,
    Task,
    TaskFinish,
    TaskLimits,
    TaskRequest,
)

_MAX_TASKS = 200
_DEFAULT_CHECKPOINT_DIR = Path("_cache") / "tasks"

_TASKS: dict[str, Task] = {}
_CONTROL: dict[str, str] = {}   # task_id -> "pause" | "cancel"
_INJECT: dict[str, list[str]] = {}


class KernelError(RuntimeError):
    """Kernel 使用方式错误（不是业务失败）。"""


# ── 规划与执行接口（依赖注入，便于离线测试）────────────────────────


class Planner(Protocol):
    async def plan(self, task: Task) -> Plan: ...

    async def reflect(self, task: Task, step: PlanStep, outcome: dict[str, Any]) -> dict[str, Any]:
        """返回 {action: continue|replan|finish, steps?: [...], conclusion?: str}"""


Executor = Callable[[Task, PlanStep], Any]


@dataclass
class KernelDeps:
    planner: Optional[Planner] = None
    executor: Optional[Executor] = None
    checkpoint_dir: Path = _DEFAULT_CHECKPOINT_DIR


# ── 默认执行器：走工具面（唯一副作用入口）────────────────────────


async def default_executor(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.tool.surface import execute as surface_execute

    if step.capability == "run_pipeline":
        # C3 才把老管线包成能力；现在明确拒绝，不假装能跑
        return {
            "status": "refused",
            "error": "run_pipeline 能力在 C3 接入（当前仅工具面可执行）",
        }
    idem = f"{task.id}:{step.id}:{step.attempt}"
    outcome = await surface_execute(
        step.capability, step.args, pipeline_id=task.id, idem_key=idem,
    )
    return outcome


# ── 默认规划器：LLM + 退化 ────────────────────────────────────


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "capability": {"type": "string"},
                    "args": {"type": "object"},
                    "why": {"type": "string"},
                },
                "required": ["capability"],
            },
        },
    },
    "required": ["steps"],
}

_REFLECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["continue", "replan", "finish"]},
        "conclusion": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "capability": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["capability"],
            },
        },
    },
    "required": ["action"],
}


class LLMPlanner:
    """用真实模型做规划（测试里注入 fake planner 即可，不需要网络）。"""

    def __init__(self, *, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec

    async def plan(self, task: Task) -> Plan:
        capabilities = _capability_catalog()
        prompt = (
            f"需求：{task.request.topic}\n"
            f"目标时长：{task.request.duration_sec or '未指定'} 秒\n"
            f"可用能力（只能从这里选）：\n{capabilities}\n\n"
            "请给出 1-6 步的执行计划。只输出 JSON，不要解释。"
        )
        try:
            import asyncio

            from clipwright.services.llm import LLMService

            data = await asyncio.wait_for(
                LLMService().structured_output(
                    system_prompt=(
                        "你是视频创作任务的规划器。只使用给定能力，按依赖顺序排列步骤；"
                        "能用一步做完就别拆成三步。"
                    ),
                    user_prompt=prompt,
                    output_schema=_PLAN_SCHEMA,
                    use_flash=True,
                    pipeline_id=task.id,
                ),
                timeout=self.timeout_sec,
            )
            steps = _steps_from(data, prefix="p")
            if steps:
                return Plan(
                    steps=steps,
                    rationale=str((data or {}).get("rationale") or "")[:500],
                )
        except Exception as e:  # noqa: BLE001 — 规划失败要有可用退化，而不是让任务空转
            logger.warning("LLM 规划失败，退化为单步计划: %s", e)
            from clipwright.services.task_events import emit

            emit(task.id, "degradation", f"规划失败，退化为单步计划: {type(e).__name__}",
                 {"error": str(e)[:300]})
        return Plan(
            steps=[PlanStep(id="p1", title="整体生成", capability="run_pipeline",
                            kind="pipeline", why="规划不可用时的最保守路径")],
            rationale="退化计划",
        )

    async def reflect(self, task: Task, step: PlanStep, outcome: dict[str, Any]
                      ) -> dict[str, Any]:
        # 执行成功 → 继续；失败/拒绝 → 交给调用方按规则收尾（不额外花一次模型调用）
        if outcome.get("status") == "ok":
            return {"action": "continue"}
        return {"action": "finish", "conclusion": f"{step.capability} 未成功：{outcome.get('error') or ''}"}


def _capability_catalog() -> str:
    """给规划器的能力清单（只列可直调档位，dangerous 不出现）。"""
    try:
        from clipwright.tool.surface import build_surface

        rows = [
            f"- {e.name}（{e.tier}）: {e.description[:60]}"
            for e in build_surface() if e.available
        ][:60]
        return "\n".join(rows) if rows else "（能力目录不可用）"
    except Exception:  # noqa: BLE001
        return "（能力目录不可用）"


def _steps_from(data: Optional[dict[str, Any]], *, prefix: str) -> list[PlanStep]:
    out: list[PlanStep] = []
    for idx, raw in enumerate((data or {}).get("steps") or [], 1):
        if not isinstance(raw, dict) or not raw.get("capability"):
            continue
        out.append(
            PlanStep(
                id=f"{prefix}{idx}",
                title=str(raw.get("title") or raw.get("capability"))[:80],
                capability=str(raw["capability"]),
                args=dict(raw.get("args") or {}),
                why=str(raw.get("why") or "")[:200],
            )
        )
    return out


# ── Kernel ──────────────────────────────────────────────


def limits_from_settings() -> TaskLimits:
    """从设置读四道闸；金额闸未配置=无上限（Q1 裁定）。"""
    limits = TaskLimits(
        max_turns=int(getattr(settings, "agent_max_turns", 30) or 30),
        max_tool_calls=int(getattr(settings, "agent_max_tool_calls", 80) or 80),
        max_wall_sec=float(getattr(settings, "agent_max_wall_sec", 3600.0) or 3600.0),
        budget_usd=getattr(settings, "pipeline_budget_usd", None),
    )
    limits.validate_or_raise()
    return limits


class AgentKernel:
    def __init__(self, deps: Optional[KernelDeps] = None) -> None:
        self.deps = deps or KernelDeps()
        self.deps.planner = self.deps.planner or LLMPlanner()
        self.deps.executor = self.deps.executor or default_executor

    # ── 生命周期 ─────────────────────────────────

    def create(self, request: TaskRequest, *, mode: str = "on") -> Task:
        task = Task(
            id=f"task_{uuid.uuid4().hex[:10]}",
            request=request,
            mode=mode,
            limits=limits_from_settings(),
        )
        _TASKS[task.id] = task
        if len(_TASKS) > _MAX_TASKS:
            oldest = next(iter(_TASKS))
            if oldest != task.id:
                _TASKS.pop(oldest, None)
        from clipwright.services.task_events import emit

        emit(task.id, "task_start", f"任务创建：{request.topic[:60]}",
             {"plan_only": request.plan_only, "mode": mode})
        self._checkpoint(task)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        task = _TASKS.get(task_id)
        if task is not None:
            return task
        loaded = self._load_checkpoint(task_id)
        if loaded is not None:
            _TASKS[task_id] = loaded
        return loaded

    def pause(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.terminal:
            raise KernelError(f"任务已结束，无法暂停: {task_id}")
        _CONTROL[task_id] = "pause"
        task.status = "paused"
        task.touch()
        self._checkpoint(task)
        return task

    def cancel(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.terminal:
            return task
        _CONTROL[task_id] = "cancel"
        from clipwright.services.task_events import emit

        task.status = "cancelled"
        task.touch()
        emit(task_id, "task_end", "任务被取消", {"reason": "cancelled"})
        self._checkpoint(task)
        return task

    def inject(self, task_id: str, text: str) -> Task:
        task = self._require(task_id)
        note = str(text or "").strip()
        if not note:
            raise KernelError("注入内容为空")
        _INJECT.setdefault(task_id, []).append(note[:500])
        from clipwright.services.task_events import emit

        emit(task_id, "inject", f"人工注入：{note[:80]}", {"text": note[:500]})
        return task

    # ── 规划 ─────────────────────────────────────

    async def plan(self, task: Task) -> Plan:
        from clipwright.services.task_events import emit

        task.status = "planning"
        plan = await self.deps.planner.plan(task)  # type: ignore[union-attr]
        task.plan = plan
        task.status = "pending"
        task.touch()
        from clipwright.services.recording import maybe_record

        maybe_record("llm", "plan", {"topic": task.request.topic}, plan.model_dump(mode="json"))
        emit(task.id, "plan", f"计划 {len(plan.steps)} 步",
             {"steps": [s.model_dump(mode="json") for s in plan.steps],
              "rationale": plan.rationale})
        self._checkpoint(task)
        return plan

    # ── 执行主循环 ────────────────────────────────

    async def run(self, task: Task) -> Task:
        from clipwright.services.task_events import emit

        started = time.monotonic()
        if task.plan is None:
            try:
                await self.plan(task)
            except Exception as e:  # noqa: BLE001 — 规划崩溃要收尾，不能让协程无声结束
                logger.warning("规划失败 %s: %s", task.id, e)
                self._finish(task, reason=f"plan_error:{type(e).__name__}: {e}")
                return task
        task.status = "running"
        task.touch()

        while True:
            pause_or_cancel = self._control_hit(task)
            if pause_or_cancel:
                break
            gate = self._gate_hit(task, started)
            if gate:
                emit(task.id, "gate_hit", f"触闸：{gate}", {"reason": gate,
                                                           "usage": task.usage.model_dump()})
                self._finish(task, reason=gate)
                break

            step = task.plan.next_step()
            if step is None:
                self._finish(task, reason="plan_completed")
                break

            task.usage.turns += 1
            outcome = await self._run_step(task, step)
            await self._after_step(task, step, outcome)
            if task.terminal:
                break

        self._checkpoint(task)
        return task

    async def resume(self, task_id: str) -> Task:
        """从检查点续跑（已完成的 Step 不重跑）。"""
        task = self.get(task_id)
        if task is None:
            raise KernelError(f"任务不存在: {task_id}")
        if task.terminal:
            return task
        _CONTROL.pop(task_id, None)
        from clipwright.services.task_events import emit

        emit(task_id, "task_start", "从检查点续跑",
             {"last_status": task.status,
              "done_steps": [s.id for s in (task.plan.steps if task.plan else []) if s.terminal]})
        if task.plan is None:
            return await self.run(task)
        task.status = "running"
        return await self.run(task)

    async def _run_step(self, task: Task, step: PlanStep) -> dict[str, Any]:
        from clipwright.services.task_events import emit

        step.status = "running"
        step.attempt += 1
        emit(task.id, "step_start", f"开始：{step.title or step.capability}",
             {"step_id": step.id, "capability": step.capability, "attempt": step.attempt,
              "why": step.why})
        emit(task.id, "tool_call", f"{step.capability}",
             {"step_id": step.id, "args": step.args, "tier_hint": step.kind})

        started = time.monotonic()
        try:
            outcome = await self.deps.executor(task, step)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001 — 执行异常不能带崩 Kernel
            outcome = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        step.duration_ms = round((time.monotonic() - started) * 1000, 2)

        # 录制放在 Kernel 而不是执行器里：这样任何执行器（真实/回放/测试）都能被录
        from clipwright.services.recording import maybe_record

        maybe_record(
            "tool", step.capability, {"step_id": step.id, "args": step.args},
            outcome, error=outcome.get("error"),
        )

        status = str(outcome.get("status") or "error")
        step.status = "done" if status in ("ok", "dry_run") else "failed"
        step.error = outcome.get("error")
        step.result_brief = _brief(outcome)
        task.usage.tool_calls += 1
        if isinstance(outcome.get("record"), dict):
            cost = outcome["record"].get("cost_usd")
            if isinstance(cost, (int, float)):
                task.usage.cost_usd += float(cost)

        emit(task.id, "tool_result", f"{step.capability} → {status}",
             {"step_id": step.id, "status": status, "brief": step.result_brief,
              "error": step.error, "duration_ms": step.duration_ms})
        emit(task.id, "step_end", f"结束：{step.title or step.capability}（{step.status}）",
             {"step_id": step.id, "status": step.status})
        return outcome

    def _observe(self, task: Task, step: PlanStep, outcome: dict[str, Any]) -> None:
        """把观察压成**结构化摘要**（A11：关键数值不许丢）。"""
        facts: dict[str, Any] = {}
        output = outcome.get("output")
        if isinstance(output, dict):
            for key, value in list(output.items())[:12]:
                if isinstance(value, (int, float, bool)):
                    facts[key] = value
                elif isinstance(value, str) and len(value) <= 120:
                    facts[key] = value
        task.context_digest.append({
            "step_id": step.id,
            "capability": step.capability,
            "status": step.status,
            "brief": step.result_brief,
            "facts": facts,
        })
        # 工作记忆有界：只保留最近 12 条观察的明细，更早的压成计数（不是丢弃语义）
        if len(task.context_digest) > 24:
            head = task.context_digest[: len(task.context_digest) - 12]
            task.context_digest = (
                [{"summary": f"更早 {len(head)} 步已压缩", "steps": len(head)}]
                + task.context_digest[-12:]
            )

    async def _reflect(self, task: Task, step: PlanStep,
                       outcome: dict[str, Any]) -> dict[str, Any]:
        """调用规划器做 reflect；异常必须被收住（否则任务协程会静默死掉）。"""
        from clipwright.services.recording import maybe_record
        from clipwright.services.task_events import emit

        try:
            decision = await self.deps.planner.reflect(task, step, outcome)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 — 回放缺失/规划器崩溃都要可见且可收尾
            emit(task.id, "degradation", f"reflect 失败: {type(e).__name__}",
                 {"error": str(e)[:300]})
            return {"action": "finish",
                    "conclusion": f"reflect_error:{type(e).__name__}: {e}"}
        maybe_record("llm", "reflect", {"step_id": step.id, "status": step.status}, decision)
        return decision

    async def _after_step(self, task: Task, step: PlanStep, outcome: dict[str, Any]) -> None:
        from clipwright.services.task_events import emit

        # 观察必须用**真实结果**（含 output），否则结构化摘要会变成空壳
        self._observe(task, step, outcome)

        decision = await self._reflect(task, step, outcome)
        action = str(decision.get("action") or "continue")
        if action == "replan":
            steps = _steps_from(decision, prefix=f"r{len(task.plan.steps) + 1}_")
            if steps:
                task.plan.steps.extend(steps)
                task.plan.version += 1
                emit(task.id, "replan", f"改计划：新增 {len(steps)} 步",
                     {"why": str(decision.get("why") or "")[:200],
                      "steps": [s.model_dump(mode="json") for s in steps]})
        elif action == "finish":
            self._finish(task, reason=str(decision.get("conclusion") or "planner_finish"))
        elif step.status == "failed":
            # 默认策略：失败即收尾（C2 不做自动重试，避免用户账单不可控）
            self._finish(task, reason=f"step_failed:{step.capability}")
        self._checkpoint(task)

    # ── 闸与收尾 ─────────────────────────────────

    def _control_hit(self, task: Task) -> Optional[str]:
        command = _CONTROL.get(task.id)
        if command == "cancel":
            task.status = "cancelled"
            return "cancel"
        if command == "pause":
            task.status = "paused"
            return "pause"
        return None

    def _gate_hit(self, task: Task, started: float) -> Optional[str]:
        task.usage.wall_sec = round(time.monotonic() - started, 2)
        limits = task.limits
        if task.usage.turns >= limits.max_turns:
            return f"回合数达到上限 {limits.max_turns}"
        if task.usage.tool_calls >= limits.max_tool_calls:
            return f"工具调用数达到上限 {limits.max_tool_calls}"
        if task.usage.wall_sec >= limits.max_wall_sec:
            return f"墙钟达到上限 {limits.max_wall_sec}s"
        if limits.budget_usd is not None and task.usage.cost_usd >= limits.budget_usd:
            return f"预算耗尽（{task.usage.cost_usd:.4f} ≥ {limits.budget_usd}）"
        return None

    def _finish(self, task: Task, *, reason: str) -> None:
        from clipwright.services.task_events import emit

        steps = task.plan.steps if task.plan else []
        completed = [s.title or s.capability for s in steps if s.status == "done"]
        unfinished = [
            f"{s.title or s.capability}（{s.status}"
            + (f"：{s.error}" if s.error else "") + "）"
            for s in steps if s.status not in ("done", "skipped")
        ]
        task.finish = TaskFinish(
            conclusion=(
                f"已完成 {len(completed)} 步"
                + (f"，未完成 {len(unfinished)} 步" if unfinished else "")
                + f"；结束原因：{reason}"
            ),
            completed=completed,
            unfinished=unfinished,
            reason=reason,
        )
        if task.status not in ("cancelled", "paused"):
            task.status = "done" if reason == "plan_completed" else "failed"
        task.touch()
        emit(task.id, "task_end", task.finish.conclusion,
             {"reason": reason, "usage": task.usage.model_dump(),
              "completed": completed, "unfinished": unfinished})
        self._checkpoint(task)

    # ── 检查点 ───────────────────────────────────

    def _checkpoint_path(self, task_id: str) -> Path:
        return Path(self.deps.checkpoint_dir) / f"{task_id}.json"

    def _checkpoint(self, task: Task) -> None:
        try:
            path = self._checkpoint_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            from clipwright.services.task_events import emit

            emit(task.id, "checkpoint", "检查点已保存",
                 {"status": task.status, "path": str(path)})
        except Exception as e:  # noqa: BLE001 — 落盘失败不阻断执行，但必须可见
            logger.warning("检查点保存失败 %s: %s", task.id, e)

    def _load_checkpoint(self, task_id: str) -> Optional[Task]:
        path = self._checkpoint_path(task_id)
        if not path.exists():
            return None
        try:
            return Task.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.warning("检查点读取失败 %s: %s", task_id, e)
            return None

    def _require(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KernelError(f"任务不存在: {task_id}")
        return task


def _brief(outcome: dict[str, Any]) -> str:
    if outcome.get("error"):
        return str(outcome["error"])[:200]
    output = outcome.get("output")
    if isinstance(output, dict):
        keys = ", ".join(list(output)[:6])
        return f"输出字段：{keys}" if keys else "无输出字段"
    if output is None:
        return "无输出"
    return str(output)[:200]


def reset_kernel_state() -> None:
    """测试用：清内存任务与控制标记（不删检查点文件）。"""
    _TASKS.clear()
    _CONTROL.clear()
    _INJECT.clear()
