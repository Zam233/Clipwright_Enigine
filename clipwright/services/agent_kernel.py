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
import asyncio
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
#: 正在跑的 asyncio.Task 句柄（真取消用）：kernel 自己持有，不再只靠 api 层
_TASK_HANDLES: dict[str, Any] = {}
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
        from clipwright.services import pipeline_capability

        return await pipeline_capability.run(task, step)
    if step.capability in ("spawn_subagent", "get_subagent_status",
                           "list_subagents", "cancel_subagent"):
        from clipwright.services import subagent_capability

        return await subagent_capability.run(task, step)
    if step.capability in ("delegate", "run_stage", "get_stage", "list_stages",
                           "wait_for_stage"):
        # `run_stage` 是旧名：老检查点里存的是它，加载时会被映射成 delegate，这里兜住存量数据
        # 阶段子代理：同步执行，结果直接回主 agent（没有"派出去不管"的语义，
        # 所以**不存在** spawn_stage 这种 fire-and-forget 别名——那会让数据绕过主 agent）。
        from clipwright.services import stage_capability

        return await stage_capability.run(task, step)
    if step.capability in ("replan_closure", "validate_stage_plan"):
        from clipwright.services import stage_closure

        return await stage_closure.run(task, step)
    if step.capability in ("run_shell", "run_python"):
        # dangerous 档不走工具面，经执行网关（判定 → 授权 → 沙盒 → 审计）
        from clipwright.services import exec_capability

        return await exec_capability.run(task, step)
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


_DECIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["continue", "replan", "finish"]},
        "say": {"type": "string"},
        "conclusion": {"type": "string"},
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
    "required": ["action"],
}


class LLMPlanner:
    """用真实模型做规划（测试里注入 fake planner 即可，不需要网络）。"""

    def __init__(self, *, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec

    async def plan(self, task: Task) -> Plan:
        capabilities = _capability_catalog()
        # 上下文走唯一入口（PRD §17.11）：文档 + 产物 + 最近报告 + digest，不再只吃压缩字符串
        try:
            from clipwright.services import context_loader

            loaded = context_loader.load(task)
            context = loaded["text"]
            from clipwright.services.task_events import emit

            emit(task.id, "context_loaded", "规划上下文已装载",
                 {"sources": loaded["sources"], "trimmed": loaded["trimmed"],
                  "bytes": loaded["bytes"], "phase": "plan"})
        except Exception:  # noqa: BLE001 — 装载失败退化为旧压缩摘要
            context = context_bundle(task)
        hints = "\n".join(f"- {h}" for h in task.hints[:8]) or "（无）"
        try:
            from clipwright.category.registry import CategoryRegistry

            _cats = "、".join(
                f"{c['id']}（{c['name']}）" for c in CategoryRegistry.list()
            ) or "无"
        except Exception:  # noqa: BLE001 — 品类列表拿不到不影响规划
            _cats = "无"
        prompt = (
            f"需求：{task.request.topic}\n"
            f"目标时长：{task.request.duration_sec or '未指定'} 秒\n"
            f"用户临时要求（必须遵守）：\n{hints}\n\n"
            f"已知上下文：\n{context}\n\n"
            f"可用能力（只能从这里选）：\n{capabilities}\n\n"
            f"可选视频类型：{_cats}（不指定则用默认类型）\n"
            "**老管线已下线**：不要再用 run_pipeline；创作按阶段派发。\n\n"
            "编排能力的用法（目录里带「编排」标记的那几个）：\n"
            "- delegate(stage=structure|material|edit|animation|audio|quality, inputs={…}, brief=…)："
            "派一个**子 agent**做真正干活的那一步：`inputs` 由你显式提供（上一份结果的产物引用），"
            "结果**只回给你**；缺输入时它会返回 needs，你决定先补哪一步。\n"
            "- 一次创作通常覆盖 structure→material→edit→animation→audio→quality，但顺序由你判断"
            "（依赖写在能力清单里；需要时可跳过、重派或换人）。\n"
            "- spawn_subagent(role, system_prompt, tools)：只派**只读**调研员（核实文件是否存在、"
            "检索资料），不要用它做阶段工作；它不能再派子代理。\n"
            "- get_stage/list_stages：查已派阶段的状态与产物。\n"
            "- 阶段失败时看 failure.category：input_insufficient=缺上游（先派缺的那一步）、"
            "timeout/model_incapable=可重试或换模型、policy_refused=不要重试直接报告。\n\n"
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
        # 退化计划也要带上下文参数：旧实现 args 全空，导致 run_pipeline 拿到空
        # category_plugin_id 直接失败（"Unknown category plugin: "）
        # P2 起管线下线 → 退化为"按依赖序派 6 个阶段"的确定性计划：
        # 这是唯一保证"规划不可用也能把片子做出来"的路径，且每一步都可观测。
        _fallback_args: dict[str, Any] = {}
        for _key in ("category_plugin_id", "persona_id"):
            _val = getattr(task.request, _key, "")
            if _val:
                _fallback_args[_key] = _val
        from clipwright.services.stage_closure import EXECUTION_ORDER

        _titles = {"structure": "分镜结构", "material": "找素材", "edit": "粗剪",
                   "animation": "动画编排", "audio": "配音字幕", "quality": "质检"}
        return Plan(
            steps=[
                PlanStep(id=f"p{i}", title=_titles.get(_stage, _stage), capability="delegate",
                         kind="subagent", args={"stage": _stage, **(_fallback_args if _stage == "structure" else {})},
                         why="规划不可用时的确定性回退：按依赖序逐阶段执行")
                for i, _stage in enumerate(EXECUTION_ORDER, 1)
            ],
            rationale="退化计划（主 Agent 规划失败 → 按阶段依赖序执行）",
        )

    async def decide(self, task: Task, step: PlanStep, outcome: dict[str, Any],
                     context_text: str) -> dict[str, Any]:
        """一次 LLM 决策（PRD §16.10.1）：成功路径也判，不只失败。

        返回 `{action: continue|replan|finish, say, steps?, rationale?}`。
        规则护栏由调用方先跑（policy_refused/permanent/needs 命中时不花模型）。
        """
        import asyncio

        from clipwright.services.llm import LLMService

        catalog = _capability_catalog()
        brief = _brief(outcome)
        failure = outcome.get("failure") or {}
        failure_txt = ""
        if isinstance(failure, dict) and failure:
            failure_txt = (
                f"\n失败：category={failure.get('category')} "
                f"retryable={failure.get('retryable')} "
                f"blocking={str(failure.get('blocking_point') or '')[:200]}"
            )
        prompt = (
            f"## 上下文\n{context_text}\n\n"
            f"## 刚完成的一步\n- capability: {step.capability}\n- 状态: {step.status}\n"
            f"- 结论: {brief}{failure_txt}\n\n"
            f"## 可用能力（只能从这里选）\n{catalog}\n\n"
            "请决定下一步：\n"
            "- continue：继续执行计划里的下一步（默认）\n"
            "- replan：改计划（给出 steps；例如补派上游、换 agent）\n"
            "- finish：收尾（给出 conclusion 一句结论）\n"
            "必须用 `say` 说明你为什么这么决定（一句人话，给用户看）。只输出 JSON。"
        )
        # 真流式（PRD §19.4 / 用户裁定）：思考逐块 emit；provider 不支持流式时回退非流式
        if bool(getattr(settings, "agent_decide_stream", True)):
            from clipwright.services.task_events import emit as _emit

            def _on_reasoning(delta: str) -> None:
                _emit(task.id, "thinking", delta[:120],
                      {"delta": delta, "done": False, "step_id": step.id})

            try:
                data = await asyncio.wait_for(
                    LLMService().structured_output_stream(
                        system_prompt="你是视频创作任务的主 agent，正在根据上一步结果决定下一步。",
                        user_prompt=prompt,
                        output_schema=_DECIDE_SCHEMA,
                        use_flash=True,
                        on_reasoning=_on_reasoning,
                    ),
                    timeout=min(self.timeout_sec, 25.0),
                )
                _emit(task.id, "thinking", "", {"delta": "", "done": True,
                                                "step_id": step.id})
                return _normalize_decision(data)
            except Exception as exc:  # noqa: BLE001 — 流式失败退回非流式
                logger.info("流式决策失败，回退非流式: %s", exc)

        data = await asyncio.wait_for(
            LLMService().structured_output(
                system_prompt="你是视频创作任务的主 agent，正在根据上一步结果决定下一步。",
                user_prompt=prompt,
                output_schema=_DECIDE_SCHEMA,
                use_flash=True,
                pipeline_id=task.id,
            ),
            timeout=min(self.timeout_sec, 25.0),
        )
        return _normalize_decision(data)

    async def reflect(self, task: Task, step: PlanStep, outcome: dict[str, Any]
                      ) -> dict[str, Any]:
        """根据结果决定下一步。

        关键点：**失败要让主代理有机会再判断一次**，而不是一律收尾。
        规则（不额外花模型调用）：
        - 成功 → continue；
        - 失败且 `failure.retryable == "retry_same"` 且这是第 1 次尝试 → retry（重跑同一步）；
        - 其它失败（needs_human / permanent / 未知）→ finish，并把类别与证据写进结论。
        """
        if outcome.get("status") == "ok":
            return {"action": "continue"}

        failure = outcome.get("failure") or {}
        retryable = str(failure.get("retryable") or "")
        category = str(failure.get("category") or "unknown")

        if retryable == "retry_same" and step.attempt < 2:
            return {
                "action": "retry",
                "why": f"{category} 可重试（第 {step.attempt} 次失败）",
            }

        evidence = failure.get("evidence") or []
        tail = ""
        if evidence:
            first = evidence[0]
            tail = f"（{first.get('kind')}: {str(first.get('text'))[:120]}）"
        return {
            "action": "finish",
            "conclusion": (
                f"{step.capability} 未成功：{category}"
                f"（retryable={retryable or 'unknown'}）{tail}"
            ),
        }


def context_bundle(task: Task, *, max_chars: int = 1200) -> str:
    """把任务上下文压成给模型的短文本（Q5：摘要优先 + 关键数值结构化保留）。

    为什么不用"把最近 N 条观察全塞进去"：长任务的观察里 90% 是噪声，
    真正有用的是**路径/数值/状态**这些可核对的事实。压缩后必须仍然包含它们，
    否则模型会基于模糊记忆做决定——那比上下文不够更糟。
    """
    lines: list[str] = []
    plan = task.plan
    if plan:
        for step in plan.steps:
            if step.status == 'pending':
                continue
            brief = f"（{step.result_brief}）" if step.result_brief else ''
            err = f" 失败：{step.error}" if step.error else ''
            lines.append(f"- [{step.status}] {step.title or step.capability}{brief}{err}")
    # 结构化事实：从 digest 里把数值/路径类字段原样保留
    facts: list[str] = []
    for entry in task.context_digest:
        step_id = str(entry.get('step_id') or '')
        for key, value in (entry.get('facts') or {}).items():
            if isinstance(value, (int, float, bool)) or (
                isinstance(value, str) and len(value) <= 80
            ):
                facts.append(f"{step_id}.{key}={value}")
    if facts:
        lines.append("关键数值：" + "；".join(facts[-24:]))
    text = "\n".join(lines) or "（无历史）"
    return text[:max_chars]


def _capability_catalog() -> str:
    """能力目录（给规划器看）。

    **必须同时包含 META_CAPABILITIES**：`build_surface()` 只收 `kind=="tool"` 的工具与
    Skill，编排能力（`delegate`/`spawn_subagent`/`replan_closure`…）从来没进过目录——
    于是主 Agent 明明有派发通道，规划时却"看不见"，从不主动派子代理（实测
    `'spawn_subagent' in catalog == False`）。
    """
    rows: list[str] = []
    try:
        from clipwright.tool.surface import build_surface

        rows = [
            f"- {e.name}（{e.tier}）: {e.description[:60]}"
            for e in build_surface() if e.available
        ][:60]
    except Exception:  # noqa: BLE001 — 工具面不可用不应连编排能力一起丢
        rows = []
    try:
        from clipwright.services.capability_matrix import META_CAPABILITIES

        # 已下线的编排能力（notes 里带「已下线」）不进目录：写进去只会诱导规划器去试，
        # 试了必然被拒，白烧一轮 token。
        rows += [
            f"- {m['name']}（{m['tier']}·编排）: {m['notes'][:80]}"
            for m in META_CAPABILITIES
            if "已下线" not in str(m.get("notes") or "")
        ]
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(rows) if rows else "（能力目录不可用）"


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
        # 真取消：把正在跑的协程打断（否则执行中的阶段要跑满墙钟才停）
        handle = _TASK_HANDLES.get(task_id)
        if handle is not None and not handle.done():
            try:
                handle.cancel()
            except Exception as exc:  # noqa: BLE001 — 取消失败只记录，仍置终态
                logger.warning("取消任务协程失败 %s: %s", task_id, exc)
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
        # C5：插话必须进**任务状态**（而不只是事件流），否则规划器根本看不见，
        # 用户会觉得"我说了它没听"。消费点在下一次 plan/reflect。
        task.hints.append(note[:500])
        task.touch()
        from clipwright.services.task_events import emit

        emit(task_id, "inject", f"人工注入：{note[:80]}", {"text": note[:500],
                                                          "pending_hints": len(task.hints)})
        self._checkpoint(task)
        return task

    # ── 规划 ─────────────────────────────────────

    async def plan(self, task: Task) -> Plan:
        from clipwright.services.task_events import emit

        task.status = "planning"
        hints_used = list(task.hints)
        plan = await self.deps.planner.plan(task)  # type: ignore[union-attr]
        task.plan = plan
        # 插话被这一版计划消费掉（否则会一直挂着，让用户以为没被听到）
        if hints_used:
            task.hints = [h for h in task.hints if h not in hints_used]
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
        # 真取消（PRD §18.10.4 / S5）：kernel 自己持有执行句柄，
        # 旧实现句柄留在 api 层，取消只置标记 → 执行中的 900s 阶段停不住。
        _TASK_HANDLES[task.id] = asyncio.current_task()  # type: ignore[assignment]
        try:
            return await self._run_loop(task, started=started)
        finally:
            _TASK_HANDLES.pop(task.id, None)

    async def _run_loop(self, task: Task, *, started: float) -> Task:
        from clipwright.services.task_events import emit

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
            if task.status == "paused":
                # 等授权：不 reflect、不推进计划，落检查点后退出主循环
                self._checkpoint(task)
                return task
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
        # 先说话再动手（PRD §16.3）：文本优先用规划器给的 `say`，其次 `why`，最后模板句。
        _intent_text = _intent_of(step)
        if _intent_text:
            emit(task.id, "intent", _intent_text,
                 {"text": _intent_text, "step_id": step.id, "agent": step.capability})
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
        if status in ("pending_approval", "needs_human_review"):
            # 等授权 / 等人审 ≠ 失败：Step 保持未完成、任务暂停，处理完 resume 继续
            from clipwright.services.task_events import emit as _emit

            step.status = "pending"
            step.result_brief = (
                f"等待授权（{outcome.get('risk')}）"
                if status == "pending_approval"
                else "等待人工审核"
            )
            emit(task.id, "approval_request",
                 (f"需要授权：{outcome.get('command') or ''}" if status == "pending_approval"
                  else f"需要人工审核子代理：{outcome.get('role') or ''}")[:120],
                 {"step_id": step.id, "approval_id": outcome.get("approval_id"),
                  "risk": outcome.get("risk"), "reasons": outcome.get("reasons"),
                  "kind": status})
            _CONTROL[task.id] = "pause"
            task.status = "paused"
            emit(task.id, "step_end", f"暂停等待处理：{step.title or step.capability}",
                 {"step_id": step.id, "status": "pending"})
            return outcome
        step.status = "done" if status in ("ok", "dry_run") else "failed"
        step.error = outcome.get("error")
        step.result_brief = _brief(outcome)
        task.usage.tool_calls += 1
        # 子代理的失败细节要进观察摘要，主代理才能在下一轮reflect里用上
        if isinstance(outcome.get("failure"), dict):
            step.error = str(outcome["failure"].get("blocking_point") or step.error or "")[:300]
        if isinstance(outcome.get("record"), dict):
            cost = outcome["record"].get("cost_usd")
            if isinstance(cost, (int, float)):
                task.usage.cost_usd += float(cost)
        # 阶段/子代理能力把成本放在顶层 `cost_usd`（没有 surface record）；
        # 不认这一份的话，主 Agent 任务的用量里永远看不到阶段开销（A7 就成空话）。
        _top_cost = outcome.get("cost_usd")
        if not isinstance(outcome.get("record"), dict) and isinstance(_top_cost, (int, float)):
            task.usage.cost_usd += float(_top_cost)
        # 成本未知的 costly 调用要计数：金额闸对"未知成本"是盲的，这件事必须可见
        if outcome.get("cost_unknown"):
            task.usage.unknown_cost_calls += 1

        emit(task.id, "tool_result", f"{step.capability} → {status}",
             {"step_id": step.id, "status": status, "brief": step.result_brief,
              "error": step.error, "duration_ms": step.duration_ms,
              "failure": outcome.get("failure")})
        emit(task.id, "step_end", f"结束：{step.title or step.capability}（{step.status}）",
             {"step_id": step.id, "status": step.status,
              "stage": step.args.get("stage") if isinstance(step.args, dict) else None,
              "summary": _summary_of_outcome(outcome),
              "cost_usd": outcome.get("cost_usd"),
              "duration_ms": step.duration_ms})
        # 阶段性汇报卡（PRD §16.4）：一步一张，紧跟上 step_end
        _emit_report(task, step, outcome)
        # 思考内容（PRD §19.4）：本步的模型推理（provider 给了才有），取走即清
        _emit_thinking(task, step)
        return outcome

    def _observe(self, task: Task, step: PlanStep, outcome: dict[str, Any]) -> None:
        """把观察压成**结构化摘要**（A11：关键数值不许丢）。

        修正（PRD §16.10.2 / 审计 P0）：旧实现只留标量与 ≤120 字串，
        `StageResult.summary`（≤500 字）与 `artifacts` / `failure.evidence` 全被丢掉，
        于是主 agent 的决策上下文里只剩"阶段名 + status"。这里把它们单独保留、不截断。
        """
        facts: dict[str, Any] = {}
        output = outcome.get("output")
        if isinstance(output, dict):
            for key, value in list(output.items())[:12]:
                if isinstance(value, (int, float, bool)):
                    facts[key] = value
                elif isinstance(value, str) and len(value) <= 120:
                    facts[key] = value
        # 失败结构也进摘要（主代理据此判断"换人/重试/收尾"）
        failure = outcome.get("failure")
        if isinstance(failure, dict):
            facts["failure_category"] = str(failure.get("category") or "")
            facts["failure_retryable"] = str(failure.get("retryable") or "")
            facts["failure_tool_calls"] = int(failure.get("tool_calls") or 0)
        if isinstance(failure, dict):
            facts["failure_blocking_point"] = str(failure.get("blocking_point") or "")[:300]
            facts["failure_needs"] = [
                f"{n.get('field')}@{n.get('from_stage') or n.get('from')}"
                for n in (failure.get("needs") or [])[:5] if isinstance(n, dict)
            ]
            facts["failure_evidence"] = [
                str(e.get("text"))[:200] for e in (failure.get("evidence") or [])[:3]
                if isinstance(e, dict)
            ]
        entry = {
            "step_id": step.id,
            "capability": step.capability,
            "status": step.status,
            "brief": step.result_brief,
            "facts": facts,
        }
        # ← 新增：不截断的"人话"摘要与产物预览（决策真正要读的东西）
        if isinstance(output, dict):
            entry["summary"] = str(output.get("summary") or "")[:600]
            writes = output.get("writes")
            if isinstance(writes, list):
                entry["writes"] = [str(w)[:40] for w in writes[:12]]
            report_path = str(output.get("report_path") or "")
            if report_path:
                entry["report_path"] = report_path
            arts = output.get("artifacts")
            if isinstance(arts, dict):
                entry["artifacts"] = {
                    str(k): _preview_of(v) for k, v in list(arts.items())[:8]
                }
        task.context_digest.append(entry)
        # 工作记忆有界：只保留最近 12 条观察的明细，更早的压成计数（不是丢弃语义）
        if len(task.context_digest) > 24:
            head = task.context_digest[: len(task.context_digest) - 12]
            task.context_digest = (
                [{"summary": f"更早 {len(head)} 步已压缩", "steps": len(head)}]
                + task.context_digest[-12:]
            )

    async def _decide(self, task: Task, step: PlanStep,
                      outcome: dict[str, Any]) -> dict[str, Any]:
        """每步一次的决策（PRD §16.10.1）：护栏先行，其余交 LLM。

        - `agent_decide_policy=off` → 只用规则（旧行为，回归基线）；
        - `always`（**默认**，用户裁定"每步决策都走 llm"）→ 每步都调 LLM 决策；
        - `auto` → 失败必判；成功的"只读/查询类"步骤按规则 continue（省一轮 token，需显式配置）。
        """
        from clipwright.config import settings

        policy = str(getattr(settings, "agent_decide_policy", "always") or "always").lower()
        ok = str(outcome.get("status") or "") == "ok"
        failure = outcome.get("failure") or {}
        retryable = str(failure.get("retryable") or "") if isinstance(failure, dict) else ""
        category = str(failure.get("category") or "") if isinstance(failure, dict) else ""

        if policy == "off":
            return await self._reflect(task, step, outcome)

        # 注入的 planner 只实现 plan/reflect（测试替身、回放器）：尊重它的脚本，
        # 不插规则、不打网络——否则"脚本说 finish、规则说 continue"会互相打架。
        decide_fn = getattr(self.deps.planner, "decide", None)
        if decide_fn is None:
            return await self._reflect(task, step, outcome)

        # 规则护栏（不花模型）
        if category in ("policy_refused",) or retryable == "permanent":
            return {"action": "finish",
                    "say": f"{category or '不可重试'}：不再重试，直接收尾",
                    "conclusion": f"{step.capability} 未成功：{category}"
                                  f"｜{str(failure.get('blocking_point') or '')[:160]}｜建议：报告用户"}
        needs = failure.get("needs") if isinstance(failure, dict) else None
        if isinstance(needs, list) and needs:
            from_stages = {str(n.get("from_stage") or n.get("from") or "")
                           for n in needs if isinstance(n, dict)}
            remaining = {s.args.get("stage") for s in (task.plan.steps if task.plan else [])
                         if s.status == "pending"}
            missing = [s for s in from_stages if s and s not in remaining]
            if missing:
                # 缺上游：直接补派，而不是原地重试同一输入（D4）
                steps = [{"capability": "delegate", "title": f"补派 {s}",
                          "args": {"stage": s}, "why": f"{step.capability} 需要它产出"}
                         for s in missing]
                return {"action": "replan", "steps": steps,
                        "say": f"{step.capability} 缺输入，先补派 {'、'.join(missing)}",
                        "why": "needs 指定了缺的上游阶段"}

        if category and retryable == "retry_same" and step.attempt < 2 and policy != "always":
            return await self._reflect(task, step, outcome)

        if policy != "always" and ok:
            # 成功的轻量步骤（查询/读取）不必花一轮模型
            if str(step.kind) in ("tool", "skill") or step.capability.startswith(
                    ("get_", "list_", "read_", "search_", "media_probe")):
                return {"action": "continue"}

        # 真决策：装载上下文 → LLM（失败/异常退回规则）
        try:
            from clipwright.services import context_loader

            loaded = context_loader.load(
                task,
                failure=failure if isinstance(failure, dict) and failure else None,
                budget_note=(f"回合 {task.usage.turns}/{task.limits.max_turns}，"
                             f"花费 {task.usage.cost_usd:.3f} USD"),
            )
            from clipwright.services.task_events import emit

            emit(task.id, "context_loaded", "决策上下文已装载",
                 {"sources": loaded["sources"], "trimmed": loaded["trimmed"],
                  "bytes": loaded["bytes"], "phase": "decide", "step_id": step.id})
            decision = await self.deps.planner.decide(  # type: ignore[union-attr]
                task, step, outcome, loaded["text"])
            _emit_thinking(task, step)   # 决策过程里的推理也进事件流
            return decision
        except Exception as exc:  # noqa: BLE001 — 决策失败要退化为规则，而不是卡死
            from clipwright.services.task_events import emit

            emit(task.id, "degradation", f"LLM 决策失败，退回规则: {type(exc).__name__}",
                 {"error": str(exc)[:200], "step_id": step.id})
            return await self._reflect(task, step, outcome)

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

        decision = await self._decide(task, step, outcome)
        action = str(decision.get("action") or "continue")
        _say = str(decision.get("say") or "").strip()
        if _say:
            # 叙述（PRD §16.3）：让用户看到"为什么这么决定"，而不只是结果
            emit(task.id, "narration", _say[:200],
                 {"text": _say[:200], "after_step": step.id, "action": action})
        if action == "retry":
            # 主代理判断「这次失败值得再试一次」→ 同一步回到 pending，下一轮重跑
            step.status = "pending"
            step.error = None
            emit(task.id, "replan", f"重试：{step.title or step.capability}",
                 {"why": str(decision.get("why") or "")[:200], "retry_step": step.id,
                  "attempt": step.attempt})
        elif action == "replan":
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
        # 收尾守门（PRD §18.10.4）：走过阶段派发的任务，没有可渲染时间线就不许宣称完成。
        # 只对"创作流程"生效（plan 里出现过 run_stage/delegate），避免影响查询类任务。
        if reason == "plan_completed" and any(
            s.capability in ("run_stage", "delegate") for s in steps
        ):
            try:
                from clipwright.services import task_blackboard as _bb

                board = _bb.get(task.id)
                timeline = board.get("final_timeline") or board.get("timeline")
                if not timeline:
                    reason = "缺少可渲染时间线（final_timeline 为空）：阶段未产出时间线或未合并"
            except Exception:  # noqa: BLE001 — 守门本身失败不阻断收尾
                pass
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
            # 状态持久化（S5）：黑板 + 阶段运行表也落盘，否则重启后黑板空、
            # 全部阶段变成 input_insufficient（等于任务重来）。
            self._write_state_sidecar(task)
            from clipwright.services.task_events import emit

            emit(task.id, "checkpoint", "检查点已保存",
                 {"status": task.status, "path": str(path)})
        except Exception as e:  # noqa: BLE001 — 落盘失败不阻断执行，但必须可见
            logger.warning("检查点保存失败 %s: %s", task.id, e)

    def _state_path(self, task_id: str) -> Path:
        return self._checkpoint_path(task_id).with_suffix(".state.json")

    def _write_state_sidecar(self, task: Task) -> None:
        """把黑板与阶段运行表写进 sidecar（<task>.state.json）。"""
        try:
            from clipwright.services import stage_subagent
            from clipwright.services import task_blackboard as _bb

            payload = {
                "blackboard": _bb.get(task.id),
                "blackboard_owners": {
                    k: _bb.owner_of(task.id, k) for k in _bb.get(task.id)
                },
                "stages": stage_subagent.list_stages(task.id),
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            path = self._state_path(task.id)
            path.write_text(json.dumps(payload, ensure_ascii=False, default=str),
                            encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — 辅助文件失败不影响主检查点
            logger.warning("状态 sidecar 写入失败 %s: %s", task.id, e)

    def _restore_state_sidecar(self, task_id: str) -> None:
        """重启后恢复黑板与阶段运行表（幂等：已有数据不覆盖）。"""
        path = self._state_path(task_id)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("状态 sidecar 读取失败 %s: %s", task_id, e)
            return
        try:
            from clipwright.services import stage_subagent
            from clipwright.services import task_blackboard as _bb

            board = data.get("blackboard") or {}
            owners = data.get("blackboard_owners") or {}
            for key, value in board.items():
                if key in _bb.get(task_id):
                    continue
                try:
                    _bb.put(task_id, key, value, by=str(owners.get(key) or ""))
                except Exception:  # noqa: BLE001 — 单键恢复失败不影响其它
                    continue
            stage_subagent.import_runs(task_id, data.get("stages") or [])
            logger.info("已从 sidecar 恢复任务状态 %s（黑板 %d 键）", task_id, len(board))
        except Exception as e:  # noqa: BLE001
            logger.warning("状态 sidecar 恢复失败 %s: %s", task_id, e)

    def _load_checkpoint(self, task_id: str) -> Optional[Task]:
        """读检查点并做**旧能力名映射**（S0-2）：`run_stage` → `delegate`。

        不改的话，改名当天所有在飞任务续跑都会落进 unknown capability。
        """
        path = self._checkpoint_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = data.get("plan") or {}
            mapped = 0
            for step in plan.get("steps") or []:
                if str(step.get("capability") or "") in ("run_stage", "spawn_stage"):
                    step["capability"] = "delegate"
                    mapped += 1
            task = Task.model_validate(data)
            if mapped:
                logger.info("检查点能力名映射：%s 里 %d 步 run_stage → delegate", task_id, mapped)
            # 顺带把黑板/阶段表恢复回来（重启续跑不丢上下文）
            self._restore_state_sidecar(task_id)
            return task
        except Exception as e:  # noqa: BLE001
            logger.warning("检查点读取失败 %s: %s", task_id, e)
            return None

    def _require(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KernelError(f"任务不存在: {task_id}")
        return task


def _normalize_decision(data: dict[str, Any] | None) -> dict[str, Any]:
    """把 LLM 的决策 JSON 归一化（动作、say、steps、conclusion）。"""
    action = str((data or {}).get("action") or "continue")
    if action not in ("continue", "replan", "finish"):
        action = "continue"
    out: dict[str, Any] = {"action": action, "say": str((data or {}).get("say") or "")[:200]}
    if action == "replan":
        out["steps"] = list((data or {}).get("steps") or [])
        out["why"] = out["say"]
    if action == "finish":
        out["conclusion"] = str((data or {}).get("conclusion") or out["say"] or "planner_finish")
    return out


def _summary_of_outcome(outcome: dict[str, Any]) -> str:
    """能力返回里的"人话摘要"（阶段子代理会带 summary；没有就空）。"""
    output = outcome.get("output")
    if isinstance(output, dict):
        return str(output.get("summary") or "")[:500]
    return ""


def _brief(outcome: dict[str, Any]) -> str:
    """一步的简短结论。

    **优先用人话摘要**（`StageResult.summary`），而不是键名——旧实现对着 dict 只回
    "输出字段：stage, status, artifacts…"，主 agent 于是永远看不到"候选素材 0 条"这种
    关键结论（审计 P0 / PRD §16.10.2）。
    """
    if outcome.get("error"):
        return str(outcome["error"])[:200]
    summary = _summary_of_outcome(outcome)
    if summary:
        return summary[:200]
    output = outcome.get("output")
    if isinstance(output, dict):
        keys = ", ".join(list(output)[:6])
        return f"输出字段：{keys}" if keys else "无输出字段"
    if output is None:
        return "无输出"
    return str(output)[:200]


def _intent_of(step: PlanStep) -> str:
    """执行前要说的一句话：`say` → `why` → 模板句。"""
    args = step.args if isinstance(step.args, dict) else {}
    say = str(args.get("say") or "").strip()
    if say:
        return say[:200]
    why = str(step.why or "").strip()
    if why:
        return why[:200]
    target = str(args.get("stage") or args.get("agent") or "").strip()
    title = str(step.title or "").strip()
    if target:
        return f"准备调用 {step.capability}（{target}）" + (f"：{title}" if title else "")
    return f"准备调用 {step.capability}" + (f"：{title}" if title else "")


def _preview_of(value: Any) -> Any:
    """产物值 → 一句预览（大对象只留形状，避免把 5MB 时间线塞进上下文）。"""
    if isinstance(value, dict):
        if value.get("preview") is not None:
            return str(value["preview"])[:200]
        if value.get("artifact"):
            return f"artifact={value['artifact']}"
        return {k: str(value[k])[:60] for k in list(value)[:4]}
    if isinstance(value, list):
        return f"{len(value)} 项"
    return str(value)[:200]


def _emit_thinking(task: Task, step: PlanStep) -> None:
    """把本步模型的 `reasoning_content` 发成 `thinking` 事件（有才有，不伪造）。"""
    try:
        from clipwright.services.llm import take_last_reasoning

        text = take_last_reasoning()
    except Exception:  # noqa: BLE001 — 取不到思考内容不影响主流程
        return
    if not text:
        return
    from clipwright.services.task_events import emit

    emit(task.id, "thinking", text[:120], {"delta": text, "done": True, "step_id": step.id})


def _emit_report(task: Task, step: PlanStep, outcome: dict[str, Any]) -> None:
    """阶段性汇报卡（PRD §16.4）：一步一张，失败时 headline 说明没做成。"""
    from clipwright.services.task_events import emit

    output = outcome.get("output") if isinstance(outcome.get("output"), dict) else {}
    status = str(outcome.get("status") or "")
    stage = str((step.args or {}).get("stage") or step.capability)
    summary = str(output.get("summary") or "").strip()
    headline = (
        f"{stage} 完成：{summary}"[:160] if summary and status == "ok"
        else (f"{stage} 没做成：{str(outcome.get('error') or summary or '未知原因')[:120]}"
              if status != "ok" else f"{stage} 完成")
    )
    bullets: list[str] = []
    if summary:
        bullets.append(summary[:200])
    writes = output.get("writes")
    if isinstance(writes, list) and writes:
        bullets.append("写入：" + "、".join(str(w)[:24] for w in writes[:6]))
    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict) and artifacts:
        bullets.append("产物：" + "、".join(list(artifacts)[:6]))
    failure = outcome.get("failure")
    if isinstance(failure, dict):
        for ev in (failure.get("evidence") or [])[:3]:
            if isinstance(ev, dict) and ev.get("text"):
                bullets.append(f"证据：{str(ev['text'])[:120]}")
    emit(task.id, "report", headline[:200], {
        "stage": stage, "status": status,
        "bullets": bullets[:5],
        "artifacts": list(artifacts)[:8] if isinstance(artifacts, dict) else [],
        "report_path": str(output.get("report_path") or ""),
        "cost_usd": outcome.get("cost_usd"),
        "duration_ms": step.duration_ms,
    })


def reset_kernel_state() -> None:
    """测试用：清内存任务与控制标记（不删检查点文件）。"""
    _TASKS.clear()
    _CONTROL.clear()
    _INJECT.clear()
