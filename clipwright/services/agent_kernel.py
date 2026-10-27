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
#: 工作记忆（context_digest）上限：有界，否则长任务会把每次工具结果都攒在内存里
_MAX_DIGEST = 24
_DEFAULT_CHECKPOINT_DIR = Path("_cache") / "tasks"

_TASKS: dict[str, Task] = {}
_CONTROL: dict[str, str] = {}   # task_id -> "pause" | "cancel"
#: 正在跑的 asyncio.Task 句柄（真取消用）：kernel 自己持有，不再只靠 api 层
_TASK_HANDLES: dict[str, Any] = {}
_INJECT: dict[str, list[str]] = {}
#: 已在本次进程里恢复过记忆的任务（避免每次 run 都读盘）
_MEMORY_LOADED: set[str] = set()


class KernelError(RuntimeError):
    """Kernel 使用方式错误（不是业务失败）。"""


# ── 规划与执行接口（依赖注入，便于离线测试）────────────────────────


class Planner(Protocol):
    async def plan(self, task: Task) -> Plan: ...


Executor = Callable[[Task, PlanStep], Any]


@dataclass
class KernelDeps:
    """Kernel 的注入点（modern-agent PRD §2.2 / §5.2）。

    只剩两项真依赖：**驱动器**（真实 tool loop 或离线假驱动器）与检查点目录。
    旧的 `planner` / `executor` 已随计划表执行一起删除——它们代表的正是
    "代码按预生成 steps[] 逐步执行"那条路。
    """

    checkpoint_dir: Path = _DEFAULT_CHECKPOINT_DIR
    #: 主循环形态覆盖（modern-agent PRD §5.2 的 `kernel_mode`）；
    #: None = 跟 `settings.kernel_mode`，显式给值优先。
    kernel_mode: Optional[str] = None
    #: tool loop 驱动器：签名 `(task, *, context_text, shadow, guard, on_tool_call)`
    #: → `{status, finish, tool_calls, stopped_reason, ...}`。None = 真实 agent_loop。
    driver: Optional[Any] = None
    #: **只用于产出"初始假设"文本**（`Plan`），不参与执行——执行路径里没有任何代码
    #: 读它。留在注入点上是为了离线测试不必打真实模型，以及让前端能显示"当初的打算"。
    planner: Optional[Any] = None


# ── 默认规划器：只产出"初始假设" ───────────────────────────────


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


async def _hypothesis_steps(data: Optional[dict[str, Any]], *,
                            prefix: str = "p") -> list[PlanStep]:
    """把模型的"计划建议"转成 PlanStep —— **只是假设**，不是要按序执行的步骤表。"""
    out: list[PlanStep] = []
    for i, raw in enumerate((data or {}).get("steps") or [], 1):
        if not isinstance(raw, dict):
            continue
        cap = str(raw.get("capability") or "").strip()
        if not cap:
            continue
        out.append(PlanStep(
            id=str(raw.get("id") or f"{prefix}{i}"),
            title=str(raw.get("title") or "")[:120],
            capability=cap,
            args=dict(raw.get("args") or {}),
            why=str(raw.get("why") or "")[:300],
        ))
    return out


class LLMPlanner:
    """产出**初始假设**（modern-agent PRD §2.2）：供提示词参照，不驱动执行。

    测试里注入 fake planner 也可以；Kernel 主循环根本不读它。
    """

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
            "- delegate_agent(stage=structure|material|edit|animation|audio|quality, "
            "inputs={…}, brief=…)：派一个**子 agent**做真正干活的那一步；`inputs` 由你显式提供"
            "（上一份结果的产物引用），结果**只回给你**；缺输入时它会返回 needs，你决定先补哪一步。\n"
            "- 一次创作通常覆盖 structure→material→edit→animation→audio→quality，但顺序由你判断"
            "（依赖写在能力清单里；需要时可跳过、重派或换人）。\n"
            "- research_agent(role, brief, tools)：只派**只读**调研员（核实文件是否存在、"
            "检索资料），不要用它做阶段工作；它不能再派子代理。\n"
            "- get_agent_run/list_agent_runs：查已派 run 的状态与产物。\n"
            "- 阶段失败时看 failure.category：input_insufficient=缺上游（先派缺的那一步）、"
            "timeout/model_incapable=可重试或换模型、policy_refused=不要重试直接报告。\n"
            "（旧名 delegate / spawn_subagent / get_stage / list_stages 仍是可用的兼容别名，"
            "新计划请用带 _agent 的名字。）\n\n"
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
            steps = await _hypothesis_steps(data, prefix="p")
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

def _record_digest(task: Task, name: str, result: dict[str, Any]) -> None:
    """把工具结果里的**结构化事实**（数值/短路径）写进 context_digest。

    这是 A11「上下文压缩后关键数值不丢」的执行侧保证：`_observe` 随计划表一起删掉了，
    但"数值与路径必须留痕"这条不变量还在，改由 tool loop 的每次调用落一次。
    """
    output = result.get("output")
    facts: dict[str, Any] = {}
    if isinstance(output, dict):
        for key, value in output.items():
            if isinstance(value, (int, float, bool)):
                facts[str(key)] = value
            elif isinstance(value, str) and len(value) <= 120:
                facts[str(key)] = value
            if len(facts) >= 8:
                break
    # 失败信息也要进 digest：模型下一轮必须看得到"上一枪为什么没打中"
    # （旧实现在 `_observe` 里做，随计划表一起删掉后要在这里补回来）
    failure = result.get("failure") or {}
    if isinstance(failure, dict) and failure:
        facts["failure_category"] = str(failure.get("category") or "")
        facts["failure_retryable"] = str(failure.get("retryable") or "")
        if "tool_calls" in failure:
            facts["failure_tool_calls"] = failure.get("tool_calls")
    if not facts:
        return
    task.context_digest.append({
        "step_id": name,
        "status": str(result.get("status") or ""),
        "facts": facts,
    })
    if len(task.context_digest) > _MAX_DIGEST:
        del task.context_digest[: len(task.context_digest) - _MAX_DIGEST]
    task.touch()


def context_bundle(task: Task, *, max_chars: int = 1200) -> str:
    """把任务上下文压成给模型的短文本（Q5：摘要优先 + 关键数值结构化保留）。

    为什么不用"把最近 N 条观察全塞进去"：长任务的观察里 90% 是噪声，
    真正有用的是**路径/数值/状态**这些可核对的事实。压缩后必须仍然包含它们，
    否则模型会基于模糊记忆做决定——那比上下文不够更糟。
    """
    lines: list[str] = []
    plan = task.plan
    if plan is not None and plan.steps:
        # 初始假设（非执行记录）：只列"当初打算做什么"，不谎称已执行
        lines.append("初始假设（仅供参照）：")
        for step in plan.steps[:8]:
            lines.append(f"- {step.title or step.capability}")
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
    def __init__(self, deps: Optional[KernelDeps] = None, *,
                 kernel_mode: Optional[str] = None) -> None:
        self.deps = deps or KernelDeps()
        # M3 之后只有两种形态：tool_loop（默认）与 shadow（跑 loop 不落副作用）。
        # 旧的 "plan" 已随计划表执行一起删除；传它只会得到一条降级告警，然后照常跑 loop。
        mode = kernel_mode or self.deps.kernel_mode or str(
            getattr(settings, "kernel_mode", "tool_loop"))
        self._mode_was_rejected = mode not in ("tool_loop", "shadow")
        self._requested_mode = mode
        self.kernel_mode = mode if not self._mode_was_rejected else "tool_loop"

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
            # 反查路径（surface 的 agent 工具按 pipeline_id 回查任务）会反复 load，
            # 这里也要淘汰，否则 `_TASKS` 只增不减（旧实现只有 create() 做 LRU）。
            _TASKS[task_id] = loaded
            while len(_TASKS) > _MAX_TASKS:
                oldest = next(iter(_TASKS))
                if oldest == task_id:
                    break
                _TASKS.pop(oldest, None)
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
        """产出**初始假设**（modern-agent PRD §2.2）。

        它不是执行计划：tool loop 每轮自己决定调什么，这份 `Plan` 只作为提示词里的
        参考，以及前端"它当初打算怎么做"的记录。
        """
        from clipwright.services.task_events import emit

        task.status = "planning"
        hints_used = list(task.hints)
        planner = self.deps.driver and None  # noqa: F841 — 驱动器不负责规划
        plan = await (self.deps.planner or LLMPlanner()).plan(task)
        task.plan = plan
        # 插话被这一版假设消费掉（否则会一直挂着，让用户以为没被听到）
        if hints_used:
            task.hints = [h for h in task.hints if h not in hints_used]
        task.status = "pending"
        task.touch()
        from clipwright.services.recording import maybe_record

        maybe_record("llm", "plan", {"topic": task.request.topic}, plan.model_dump(mode="json"))
        emit(task.id, "plan", f"初始假设 {len(plan.steps)} 步（供参照，非执行表）",
             {"steps": [s.model_dump(mode="json") for s in plan.steps],
              "rationale": plan.rationale, "hypothesis_only": True})
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
        """主循环入口：只有 tool-use loop 一条路（modern-agent PRD §2.2 / M3）。

        `kernel_mode` 只剩 `tool_loop`（默认）与 `shadow`（跑 loop 不落副作用）。
        旧 `plan` 的**计划表执行已物理删除**——没有计划表适配器可回退，
        传入旧值只会得到一条降级告警。

        条件不具备（无凭据 / provider 不支持 native tool calls）时**如实失败**，
        不再偷偷换一条"按 steps[] 执行"的路：那条路正是本次要拆掉的东西。
        """
        from clipwright.services import agent_loop
        from clipwright.services.task_events import emit

        mode = str(self.kernel_mode)
        if self._mode_was_rejected:
            emit(task.id, "degradation",
                 f"kernel_mode={self._requested_mode} 已随计划表执行一起下线，"
                 "按 tool_loop 运行",
                 {"kind": "kernel_mode_removed", "requested": self._requested_mode})
        available, why = agent_loop.tool_loop_available()
        if not available:
            emit(task.id, "degradation", f"tool loop 不可用：{why}",
                 {"kind": "kernel_tool_loop_unavailable", "reason": why})
            task.status = "failed"
            task.finish = TaskFinish(
                conclusion=f"无法执行：模型侧不满足 tool-use loop 条件（{why}）",
                completed=[], unfinished=[f"tool_loop:{why}"],
                reason=f"tool_loop_unavailable:{why}",
            )
            task.touch()
            emit(task.id, "task_end", task.finish.conclusion,
                 {"reason": task.finish.reason})
            self._checkpoint(task)
            return task
        return await self._run_tool_loop(task, started=started,
                                        shadow=(mode == "shadow"))

    async def _run_tool_loop(self, task: Task, *, started: float,
                             shadow: bool = False) -> Task:
        """原生 tool-use loop：不预生成步骤表，模型每轮自主决定调哪个工具。"""
        from clipwright.services import agent_loop, context_loader
        from clipwright.services.task_events import emit

        task.status = "running"
        task.touch()
        _TASK_HANDLES[task.id] = asyncio.current_task()  # type: ignore[assignment]

        # 记忆跨重启（closeout B5）：进 loop 前把该任务的情景记忆与全局语义记忆恢复回来。
        # 只在**首次**进 loop 时做（同一进程内重复 run 不重复读盘）。
        if task.id not in _MEMORY_LOADED:
            _MEMORY_LOADED.add(task.id)
            try:
                from clipwright.services import memory as _mem
                from clipwright.services.workspace import Workspace

                _root = None
                try:
                    _ws = Workspace(str(task.id))
                    if _ws.exists:
                        _root = _ws.dirs().root
                except Exception:  # noqa: BLE001 — 没有工作区就不恢复
                    _root = None
                if _root is not None:
                    stats = _mem.load(_root, task_id=task.id)
                    if stats.get("semantic") or stats.get("episodic"):
                        emit(task.id, "context_loaded", "记忆已恢复",
                             {"memory": stats, "root": str(_root)}, span="memory")
            except Exception as exc:  # noqa: BLE001 — 记忆恢复失败不阻断开工
                logger.warning("记忆恢复失败 %s: %s", task.id, exc)

        # 上下文装配（PRD §2.5 / §17.11）：按预算裁剪并记 context_loaded
        context_text = ""
        try:
            loaded = context_loader.load(task)
            context_text = loaded.get("text", "")
            emit(task.id, "context_loaded", "上下文已装载",
                 {"bytes": loaded.get("bytes", 0), "sources": loaded.get("sources", []),
                  "trimmed": loaded.get("trimmed", False)})
        except Exception as exc:  # noqa: BLE001 — 上下文失败不能阻断主循环
            logger.warning("上下文装载失败 %s: %s", task.id, exc)

        def _guard() -> Optional[str]:
            hit = self._control_hit(task)
            if hit:
                return hit
            return self._gate_hit(task, started)

        def _on_tool_call(name: str, result: dict[str, Any],
                          args: Optional[dict[str, Any]] = None) -> None:
            task.usage.tool_calls += 1
            cost = result.get("cost_usd")
            if isinstance(cost, (int, float)):
                task.usage.cost_usd = round(task.usage.cost_usd + float(cost), 6)
            elif result.get("cost_unknown"):
                # 成本未知要计数：金额闸对它是盲的，不能当成 0 悄悄过
                task.usage.unknown_cost_calls += 1
            _record_digest(task, name, result)
            # 录制（A2 回放的数据来源）：M3 之后没有步骤级执行，录制点在这里
            from clipwright.services.recording import maybe_record

            maybe_record("tool", name, dict(args or result.get("args") or {}), result,
                         error=result.get("error"))
            status = str(result.get("status") or "")
            if status in ("pending_approval", "needs_human_review"):
                # 等授权/等人审 ≠ 失败：任务暂停，处理完 resume 继续（PRD A1 / §18.10.4）
                emit(task.id, "approval_request",
                     f"工具 {name} 等待授权",
                     {"tool": name, "approval_id": result.get("approval_id"),
                      "risk": result.get("risk"), "note": result.get("note")},
                     span="tool")
                _CONTROL[task.id] = "pause"
                task.status = "paused"

        # dry-run 预览（closeout B2）：用户在 proceed 勾了"只预览"时，Task 侧
        # 唯一的口径就是 `request.extra["dry_run"]`。透传给 tool loop，
        # 由它把原子工具降级成结构预检、拒绝派发子 agent。
        dry_run = bool((getattr(task.request, "extra", None) or {}).get("dry_run"))

        task.usage.turns += 1
        driver = self.deps.driver or agent_loop.run_loop
        try:
            outcome = await driver(
                task, context_text=context_text, shadow=shadow,
                guard=_guard, on_tool_call=_on_tool_call, dry_run=dry_run,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 驱动器异常要如实收尾，不许无声结束
            # 注意：`RecordingMiss` 这类"回放缺数据"必须原样出现在原因里，
            # 否则会退化成"任务莫名结束"（A2 的红线）。
            logger.warning("tool loop 驱动器异常 %s: %s", task.id, exc)
            outcome = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "stopped_reason": type(exc).__name__,
                "failure": {"category": "internal_defect", "retryable": "needs_human",
                            "blocking_point": f"{type(exc).__name__}: {exc}"[:200],
                            "evidence": []},
            }
        # 模型轮次计入 turns（tool loop 自己知道跑了几轮），回合闸才有意义
        extra_rounds = int(outcome.get("rounds") or 0)
        if extra_rounds > 1:
            task.usage.turns += extra_rounds - 1
        task.usage.wall_sec = round(time.monotonic() - started, 2)

        status = str(outcome.get("status") or "error")
        reason = str(outcome.get("stopped_reason") or "")
        if status == "aborted":
            # 取消/暂停/触闸要分清，不能一律 paused：
            # - 人工**暂停**不是结局 → 保持 paused、不写终态、不记指标（resume 后还会继续）；
            # - **取消**是终态 → cancelled；
            # - **触闸**（回合/工具调用/墙钟/预算）是收尾（PRD A1：结论+已完成+未完成），
            #   一直挂着 paused 会让它永远不进任务成功率分母。
            # 事件由 **Kernel** 发（不依赖驱动器实现）：谁拥有闸，谁负责留痕。
            emit(task.id, "gate_hit", f"触闸：{reason or outcome.get('error') or '已中止'}",
                 {"reason": reason or outcome.get("error"),
                  "tool_calls": task.usage.tool_calls,
                  "usage": task.usage.model_dump()}, span="kernel")
            text = str(reason or outcome.get("error") or "")
            if task.status in ("cancelled", "paused") or text == "cancel":
                task.status = "cancelled" if text == "cancel" else task.status
                if task.status == "cancelled":
                    task.finish = TaskFinish(
                        conclusion="任务被取消", completed=[],
                        unfinished=["cancel"], reason="cancelled")
                    self._record_task_metrics(task, "cancelled")
                self._checkpoint(task)
                return task
            if text == "pause":
                task.status = "paused"
                self._checkpoint(task)
                return task
            # 触闸 → 收尾（不是失败也不是成功：如实写原因）
            gate_reason = f"gate:{text}"
            task.finish = TaskFinish(
                conclusion=f"触闸收尾：{text}",
                completed=[], unfinished=[text], reason=gate_reason)
            task.status = "failed"
            task.touch()
            emit(task.id, "task_end", task.finish.conclusion,
                 {"reason": gate_reason, "usage": task.usage.model_dump()},
                 span="kernel")
            self._record_task_metrics(task, gate_reason)
            self._checkpoint(task)
            return task

        finish = outcome.get("finish") or {}
        if status == "ok":
            conclusion = str(finish.get("conclusion") or "")
            # 收尾守门（§18.10.4）：声称完成但黑板里没有可渲染时间线 → 如实标失败
            gate_reason = "dry_run_preview" if dry_run else "tool_loop_completed"
            if not shadow and not dry_run:
                try:
                    from clipwright.services import task_blackboard as _bb

                    board = _bb.get(task.id)
                    if not (board.get("final_timeline") or board.get("timeline")):
                        gate_reason = "缺少可渲染时间线（final_timeline 为空）"
                except Exception:  # noqa: BLE001 — 守门失败不阻断收尾
                    pass
            task.finish = TaskFinish(
                conclusion=conclusion or f"工具循环结束：{len(outcome.get('tool_calls') or [])} 次调用",
                completed=[], unfinished=[],
                reason=gate_reason,
            )
            task.status = ("done" if gate_reason in ("tool_loop_completed",
                                                    "dry_run_preview") else "failed")
            # 结果快照（含质检摘要）落一份：前端任务详情/质检页读它，而不是读老管线的结果字典
            try:
                from clipwright.services import task_blackboard as _bb2
                from clipwright.services import task_results

                task_results.remember_from_board(task.id, _bb2.get(task.id))
            except Exception as exc:  # noqa: BLE001 — 快照失败不影响收尾
                logger.warning("结果快照写入失败 %s: %s", task.id, exc)
            task.touch()
            emit(task.id, "task_end", task.finish.conclusion,
                 {"reason": task.finish.reason, "usage": task.usage.model_dump(),
                  "tool_calls": len(outcome.get("tool_calls") or [])},
                 run_id=str(finish.get("run_id") or ""), span="kernel")
            self._record_task_metrics(task, gate_reason)
            self._checkpoint(task)
            return task

        failure = outcome.get("failure") or {}
        category = str(failure.get("category") or "")
        task.finish = TaskFinish(
            conclusion=(f"工具循环失败：{outcome.get('error') or reason}"
                        + (f"（category={category}，"
                           f"retryable={failure.get('retryable') or 'unknown'}）"
                           if category else "")),
            completed=[], unfinished=[f"tool_loop:{reason}"],
            reason=f"tool_loop_error:{reason}",
        )
        task.status = "failed"
        task.touch()
        emit(task.id, "task_end", task.finish.conclusion,
             {"reason": task.finish.reason, "failure": failure,
              "usage": task.usage.model_dump()}, span="kernel")
        self._record_task_metrics(task, task.finish.reason)
        self._checkpoint(task)
        return task

    async def resume(self, task_id: str) -> Task:
        """从检查点续跑。

        M3 之后没有"已完成 Step 不重跑"这回事（没有 Step 了）：续跑就是**重新进
        tool loop**——模型看着已有产物与报告继续干。幂等由工具面的 `idem_key`
        与 `agent_run` 的 run 缓存兜住，而不是靠"步骤已完成"标记。
        """
        task = self.get(task_id)
        if task is None:
            raise KernelError(f"任务不存在: {task_id}")
        if task.terminal:
            return task
        _CONTROL.pop(task_id, None)
        from clipwright.services.task_events import emit

        emit(task_id, "task_start", "从检查点续跑",
             {"last_status": task.status, "usage": task.usage.model_dump()})
        task.status = "running"
        return await self.run(task)

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
        """收尾（M3 后不再按 steps 汇总：没有步骤表，只有一次 tool loop 的结果）。

        仍保留"没有可渲染时间线就不许宣称完成"的守门（§4.4 领域不变量）：
        只要这次任务碰过阶段派发，就必须有 `final_timeline`。
        """
        from clipwright.services.task_events import emit

        completed: list[str] = []
        unfinished: list[str] = []
        if reason in ("tool_loop_completed", "plan_completed"):
            try:
                from clipwright.services import task_blackboard as _bb

                board = _bb.get(task.id)
                timeline = board.get("final_timeline") or board.get("timeline")
                if timeline:
                    completed.append("final_timeline")
                else:
                    reason = "缺少可渲染时间线（final_timeline 为空）"
                    unfinished.append("final_timeline")
            except Exception:  # noqa: BLE001 — 守门本身失败不阻断收尾
                pass
        task.finish = TaskFinish(
            conclusion=f"结束原因：{reason}",
            completed=completed,
            unfinished=unfinished,
            reason=reason,
        )
        if task.status not in ("cancelled", "paused"):
            task.status = "done" if reason in ("tool_loop_completed",
                                               "plan_completed") else "failed"
        task.touch()
        emit(task.id, "task_end", task.finish.conclusion,
             {"reason": reason, "usage": task.usage.model_dump(),
              "completed": completed, "unfinished": unfinished})
        self._record_task_metrics(task, reason)
        self._checkpoint(task)

    def _persist_memory(self, task: Task, reason: str) -> None:
        """收尾把记忆落盘：任务结局写一条情景记忆 → 追加落盘 + 语义快照。"""
        try:
            from clipwright.services import memory as _mem
            from clipwright.services.workspace import Workspace

            ok = str(task.status) in ("done",)
            conclusion = (task.finish.conclusion if task.finish else "") or reason
            try:
                _mem.remember(
                    f"任务 {task.status}：{conclusion}",
                    task_id=task.id, layer="episodic",
                    kind="outcome" if ok else "failure",
                    provenance={"source": "kernel", "reason": reason},
                    pinned=not ok,
                )
            except Exception as exc:  # noqa: BLE001 — 记忆写入失败不阻断收尾
                logger.warning("任务情景记忆写入失败 %s: %s", task.id, exc)

            _ws = Workspace(str(task.id))
            if not _ws.exists:
                return
            root = _ws.dirs().root
            _mem.dump_episodic(task.id, root)
            _mem.dump_semantic(root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆落盘失败 %s: %s", task.id, exc)

    def _record_task_metrics(self, task: Task, reason: str) -> None:
        """任务级指标（PRD §2.6.1）：CompletionGate 通过率的分母就在这里。

        顺带落盘记忆：所有终态路径都会走到这里，是唯一不会漏的挂点。
        """
        self._persist_memory(task, reason)
        try:
            from clipwright.services import evaluation

            evaluation.record_task(
                task_id=task.id, status=task.status, reason=reason,
                turns=task.usage.turns, tool_calls=task.usage.tool_calls,
                duration_ms=int(float(task.usage.wall_sec or 0) * 1000),
                cost_usd=task.usage.cost_usd or None,
            )
        except Exception as exc:  # noqa: BLE001 — 记账失败不影响收尾
            logger.warning("任务指标记账失败 %s: %s", task.id, exc)

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
        """读检查点，并把**旧的计划表痕迹**转成迁移注记（M3）。

        旧检查点里有 `plan.steps` 与 `run_stage`/`spawn_stage` 这类能力名。M3 之后
        没有任何代码会按 steps 执行，所以它们必须降级成"当初的打算"，而不是留在
        `pending` 里假装还能续跑——那会让人以为任务有进度，实际一步也没走。
        """
        path = self._checkpoint_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = data.get("plan") or {}
            steps = plan.get("steps") or []
            legacy = [s for s in steps
                      if str(s.get("capability") or "") in ("run_stage", "spawn_stage",
                                                            "delegate", "spawn_subagent")]
            if steps:
                # 保留标题供前端展示"当初打算怎么做"，但打上"仅假设"的标记
                note = (
                    f"检查点来自计划表执行时代（{len(steps)} 步）；M3 之后不再按步骤执行，"
                    "这些只是当初的假设。续跑会重新进入 tool loop。"
                )
                data.setdefault("context_digest", []).append(
                    {"step_id": "migration", "status": "noted",
                     "facts": {"migrated_steps": len(steps),
                               "legacy_capabilities": len(legacy),
                               "note": note}})
                plan["hypothesis_only"] = True
                for step in steps:
                    if str(step.get("status") or "pending") == "pending":
                        step["status"] = "skipped"
                data["plan"] = plan
                logger.info("检查点迁移 %s：%d 步降级为假设", task_id, len(steps))
            task = Task.model_validate(data)
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
    """把本步的思考内容发成 `thinking` 事件（有才有，不伪造）。

    B5（2026-10-28 PRD）：思考来源改为**按 task_id 隔离**的缓冲
    （`agent_loop.take_thinking`），不再读全局单槽 `_LAST_REASONING`——并发子任务
    会互相覆盖，A 的思考被 B 取走属于串台。tool_loop 模式下 thinking 已在
    `run_loop` 里逐轮实时发出，这里只服务 plan 适配器的收尾补齐。
    """
    try:
        from clipwright.services.agent_loop import take_thinking

        text = take_thinking((task.id if hasattr(task, "id") else "") or "")
    except Exception:  # noqa: BLE001 — 取不到思考内容不影响主流程
        return
    if not text:
        return
    from clipwright.services.task_events import emit

    emit(task.id, "thinking", text[:120],
         {"delta": text, "done": True, "step_id": step.id})


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
    _MEMORY_LOADED.clear()
    """测试用：清内存任务与控制标记（不删检查点文件）。"""
    _TASKS.clear()
    _CONTROL.clear()
    _INJECT.clear()
