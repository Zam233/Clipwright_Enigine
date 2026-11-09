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
from datetime import datetime, timezone
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
    EscrowState,
    GoalStatus,
    TerminalReason,
)

_MAX_TASKS = 200
#: 工作记忆（context_digest）上限：有界，否则长任务会把每次工具结果都攒在内存里
_MAX_DIGEST = 24
#: 无进展阈值（用户 2026-11-02 定 N=6）：连续这么多次闸门检查没有任何新进展就收尾
_NO_PROGRESS_LIMIT = int(getattr(settings, "agent_no_progress_limit", 0) or 6)
#: 跨阶段重复动作熔断阈值（同一工具+参数失败累计次数）
_REPEAT_FAIL_LIMIT = int(getattr(settings, "agent_repeat_fail_limit", 0) or 5)
#: task_id → {"sig": 进展指纹, "same": 连续无进展次数}
_PROGRESS: dict[str, dict[str, Any]] = {}
#: task_id → {参数指纹: 累计失败次数}（跨阶段，与 agent_loop 单轮 fail_streak 互补）
_REPEAT_FAILS: dict[str, dict[str, int]] = {}

_DEFAULT_CHECKPOINT_DIR = Path("_cache") / "tasks"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _goal_status_value(task: Any) -> str:
    """Goal 状态的字符串值（枚举或裸字符串都吃）。"""
    status = getattr(getattr(task, "goal", None), "status", "")
    return str(getattr(status, "value", status) or "")


def _workspace_root_of(task: Any) -> str:
    """任务工作区根（收尾报告与规划书都往这里落）。

    取值与 `_write_plan_artifact` 完全一致：先看 `request.extra.workspace_root`，
    没有就按任务 id 开一个工作区。**两边必须同一套**，否则收尾报告会写到另一个目录，
    续跑时读不到。
    """
    extra = dict(getattr(getattr(task, "request", None), "extra", {}) or {})
    root = str(extra.get("workspace_root") or "").strip()
    if root:
        return root
    try:
        from clipwright.services.workspace import Workspace

        return str(Workspace(str(getattr(task, "id", "") or "")).dirs().root)
    except Exception:  # noqa: BLE001 — 拿不到工作区就退回相对目录
        return "_cache/workspaces"

_TASKS: dict[str, Task] = {}
_CONTROL: dict[str, str] = {}   # task_id -> "pause" | "cancel"
#: 正在跑的 asyncio.Task 句柄（真取消用）：kernel 自己持有，不再只靠 api 层
_TASK_HANDLES: dict[str, Any] = {}
_INJECT: dict[str, list[str]] = {}
#: 已在本次进程里恢复过记忆的任务（避免每次 run 都读盘）
_MEMORY_LOADED: set[str] = set()

#: 收尾守门"声称完成但没有 final_timeline"时，最多再推几轮。
#:
#: E2E 2026-10-28 实测：模型把 material 跑完就自己收尾（"我读一下确认内容，然后派
#: edit 阶段"然后就结束了），守门照实判失败——但用户要的是成片。这类"提前收尾"
#: 属于模型行为，不该直接判死刑：带着明确指令再推它一把，仍然出不来才判失败。
_MAX_TIMELINE_CONTINUATIONS = 2
_TIMELINE_CONTINUATIONS: dict[str, int] = {}

# ── 降级决策守门（E2E 2026-10-29 用户裁定）──────────────────────────────
# 「截断与回退不能被主 Agent 默认接受」：阶段内部一旦 note_degradation（LLM JSON
# 截断/格式坏、自批判被跳过、模板兜底、motion gate 退让、类型化回退 SVG…），
# Kernel 必须把这件事摆到模型面前**并要求当轮决策**（重派 / 换策略 / 明确接受并写影响），
# 而不是让它继续照常往下走。这里只强推一次：够它做决定，又不会把简单场景拖成死循环。
_MAX_DEGRADATION_CONTINUATIONS = 1
#: 模型提前收尾且"再推一把"也用完时，Kernel 自己补派缺失阶段的次数上限。
#: 为什么要 Kernel 兜底：模型是概率的，收尾时机不稳；而"缺 final_timeline"是
#: **确定性**事实——提示词推两轮还不做，就该由运行时按同一套工具面自己补
#: （记录为一次真实工具调用，不是偷偷绕过）。
_MAX_KERNEL_RECOVERY = 2
_KERNEL_RECOVERY: dict[str, int] = {}
#: "最后一次收据"机会：守门即将判失败时，再给模型**一次**只做收据的机会。
#: 为什么要单独一个预算：收据不是"再干一轮活"，而是一次明确的决策动作；
#: 和它共用续跑预算会出现"预算被前面用掉 → 到收尾时连写收据的机会都没有"。
_MAX_FINAL_RECEIPT = 1
_FINAL_RECEIPT: dict[str, int] = {}
_FINAL_RECEIPT_HINT_HEAD = (
    "\n\n## 最后一步：写降级收据（只做这件事）\n"
    "以下降级还没有收据，任务会在你收尾时判失败：\n"
)
_FINAL_RECEIPT_HINT_TAIL = (
    "\n\n现在**只允许**做一件事：调用 `acknowledge_degradation`，"
    "kinds 填上面列出的 kind，reason 写清楚「为什么可以接受」（≥10 字，写真实理由），"
    "impact 写「观众会看到什么」。写完立刻 finish，不要再调用任何其他工具。\n"
    "如果你认为某项必须修，就改为派该阶段重做（delegate_agent 带 refresh=true），"
    "但不要两者都不做就收尾。"
)
#: 允许 Kernel 补派的阶段（时间线生产者 + 素材/动画：它们最常上报可修降级）
_TIMELINE_STAGE_FOR_RECOVERY = ("edit", "animation", "audio", "material")
_DEGRADATION_CONTINUATIONS: dict[str, int] = {}
#: 未处置降级：task_id → [degradation dict]
_PENDING_DEGRADATIONS: dict[str, list[dict[str, Any]]] = {}
_DEGRADATION_DECISION_HINT_HEAD = (
    "\n\n## 降级必须处置（必须遵守）\n"
    "上一步**完成了但打了折扣**，不是干净的 ok。以下是阶段上报的降级事实：\n"
    "处置不是你自己拍板就完事——**影响观众可见结果且存在真实取舍的降级，必须先向用户解释并询问**，"
    "等回答后再动手（`ask_user`，任务会进入 waiting_user）。\n"
)
_DEGRADATION_DECISION_HINT_TAIL = (
    "\n按下面的顺序处置，**不允许直接收尾或忽略**：\n"
    "1. **先判断要不要问用户**：\n"
    "   - 要问（走 `ask_user`）：`material_placeholder`（占位画面/纯色卡/文字卡）、"
    "`text_motion_weak`（文字动效弱到像贴图）、`stage_tool_failures`（工具失败率过高）"
    "这类**观众一眼能看出来**、且两条路各有代价的降级；\n"
    "   - 不用问：`RECEIPT_EXEMPT_KINDS`（llm_json_* / mg_critique_skipped / text_repair / "
    "ask_user_timeout_default）与纯内部取舍，直接按下面 3/4 处理。\n"
    "2. **问的时候必须讲人话**，一句话说清四件事，总长 ≤120 字：\n"
    "   - 现象：哪个阶段、什么事实（带数字，例如「位移只有 24px，门槛 4%」）；\n"
    "   - 影响：观众会看到什么（例如「这行字像静态贴图」）；\n"
    "   - 方案 A / B：各自代价（多花多久、可能仍不完美）；\n"
    "   - 推荐与理由，然后明确问「选哪个」/「要不要按 A 重派」。\n"
    "   `ask_user` 必须给 ≥2 个选项（每个选项 label ≤120 字，detail 写代价），"
    "需要用户补充想法时 `allow_free_text=true`；超时想继续就给 `default_option_id`。\n"
    "3. 用户选了「修」→ 按类型重派：\n"
    "   - `material_placeholder` → 重派 `material`，换更泛的英文关键词、放宽阈值或换素材源；\n"
    "   - 偶发/可救（LLM JSON 截断、自批判被跳过）→ 用同样 inputs **重派同一阶段**；\n"
    "   - 结构性短板（类型化生成失败、模板兜底、motion gate 退让）→ **换策略**"
    "（换阶段/改提示词/降低该段复杂度），并说明为什么这样选。\n"
    "4. 用户选了「保留」或本就无需问 → 调用 "
    "`acknowledge_degradation(kinds=[...], reason=..., impact=...)` 写 accepted 收据"
    "（reason ≥10 字）。**用户说「可以接受」不等于收据**：收据只能由工具写，"
    "否则任务会判 `degradation_unresolved`。\n"
    "禁止用一句「动画已编排完成」或「素材已就绪」把降级带过去；"
    "也禁止既不问用户、也不重派、也不写收据就继续往下跑。"
)
_TIMELINE_CONTINUE_HINT = (
    "\n\n## 收尾守门（必须遵守）\n"
    "上一轮结束时黑板里**还没有 `final_timeline`**，所以任务不算完成。请立刻继续推进：\n"
    "1. 若还没有 timeline → `delegate_agent(stage=\"edit\")`"
    "（script_skeleton / scenes / candidate_clips 都在黑板上，显式输入可以不传或传空，会自动回退）；\n"
    "2. 再派 `animation`；\n"
    "3. **必须再派 `audio`** —— 只有 audio 阶段会落下 `final_timeline`，"
    "并把上传配音铺进音频轨、把字幕对齐到配音；跳过 audio = 成片没有声音；\n"
    "4. 只有在黑板里出现 `final_timeline` 之后，才能收尾汇报。\n"
    "不要再说\"接下来我准备…\"这类话，直接调用工具。"
)


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


def _collect_degradations(task: Task, name: str, result: dict[str, Any]) -> None:
    """把一次工具结果里的降级登记成"待处置"，并让事件流与模型都看得见。

    为什么要在 Kernel 侧收集而不是只靠阶段自己报：降级是**编排层必须决策**的事实。
    收集点放在唯一入口（`_on_tool_call`），任何工具/阶段只要按 F9 登记了
    degradation 都会自动上浮，不需要每个 Agent 各自接线。
    """
    items: list[dict[str, Any]] = []
    for bucket in (result.get("output"), result):
        if not isinstance(bucket, dict):
            continue
        raw = bucket.get("degradations")
        if isinstance(raw, list):
            items.extend([d for d in raw if isinstance(d, dict)])
    if not items:
        return
    # 写进台账（PRD modern-agent-review P0）：降级处置需要**收据**，
    # 不能只靠"再推一轮 + 模型口头接受"。`register` 负责 (kind, scope) 去重。
    try:
        from clipwright.services import degradation_ledger

        fresh = degradation_ledger.register(task.id, items)
    except Exception:  # noqa: BLE001 — 台账不可用时退回内存去重
        pending = _PENDING_DEGRADATIONS.setdefault(task.id, [])
        known = {(str(d.get("kind")), str(d.get("scope"))) for d in pending}
        fresh = [d for d in items if (str(d.get("kind")), str(d.get("scope"))) not in known]
        pending.extend(fresh)
    if not fresh:
        return
    # 进度播报（PRD §14）：首次降级——每类 kind 只播一次，写清影响与下一步。
    try:
        from clipwright.services import progress as _progress

        for _item in fresh[:5]:
            _kind = str(_item.get("kind") or "")
            _progress.emit_progress(
                task.id, trigger="degradation_first",
                stage=str(_item.get("scope") or ""),
                action=f"降级：{_kind}",
                conclusion=str(_item.get("detail") or "")[:160],
                next_action=str(_item.get("impact") or "需要主 agent 处置")[:160],
                once_key=f"deg:{_kind}:{_item.get('scope') or ''}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("进度播报（首次降级）失败 %s: %s", task.id, exc)
    # 新降级 = 新事实 → 重置"要求决策"的续跑预算。
    # 否则前期一次 gate 续跑会把预算用光，后期完全不同的降级就没机会被响应
    # （E2E 2026-10-30 实测：animation 反复降级用掉预算，最后 audio 定稿后 4 项降级
    #  直接判失败，即使成片其实已经产出了）。
    try:
        _DEGRADATION_CONTINUATIONS[task.id] = 0
    except Exception:  # noqa: BLE001
        pass
    # 内存镜像（兼容既有观察者/测试）：台账是收据来源，内存 dict 仍保留一份
    # 便于 debug 与「当前待处置清单」的快速读取。
    _PENDING_DEGRADATIONS.setdefault(task.id, []).extend(fresh)
    try:
        from clipwright.services.task_events import emit

        emit(task.id, "degradation",
             f"{name} 上报 {len(fresh)} 项降级："
             + "、".join(sorted({str(d.get("kind")) for d in fresh}))[:120],
             {"tool": name, "count": len(fresh), "items": fresh[:8]}, span="kernel")
    except Exception:  # noqa: BLE001 — 事件通道不可用不影响执行
        pass


def _mark_stage_remediated(task_id: str, stage: str) -> None:
    """重派某阶段并成功后，把该阶段的降级记为 `remediated` 收据。"""
    if not stage:
        return
    try:
        from clipwright.services import degradation_ledger

        n = degradation_ledger.mark_remediated(task_id, stage)
        if n:
            from clipwright.services.task_events import emit

            emit(task_id, "degradation",
                 f"{stage} 阶段重派成功，{n} 项降级已修复（remediated 收据）",
                 {"stage": stage, "resolved": n, "mode": "remediated"}, span="kernel")
    except Exception:  # noqa: BLE001
        pass


async def _kernel_recover_stage(task: Any, stage: str, reason: str) -> dict[str, Any]:
    """Kernel 自己补派一个阶段（确定性补救）。

    与模型派发走**同一个入口**（`tool.surface.execute`），因此同样计入 usage、
    同样进事件流、同样有幂等键与审计——区别只是发起者是运行时而不是模型。
    成功且未回放 → 给该阶段的降级写 `remediated` 收据（真修复）。
    """
    from clipwright.services.task_events import emit
    from clipwright.tool import surface as _surface

    if not bool(getattr(settings, "agent_kernel_recovery", True)):
        emit(task.id, "narration", f"Kernel 补派已关闭，跳过 {stage}（{reason}）",
             {"kind": "kernel_recovery_disabled", "stage": stage}, span="kernel")
        return {"status": "skipped", "error": "agent_kernel_recovery=false"}
    emit(task.id, "narration", f"Kernel 补派 {stage}（{reason}）",
         {"kind": "kernel_recovery", "stage": stage, "reason": reason}, span="kernel")
    try:
        result = await _surface.execute("delegate_agent", {"stage": stage},
                                        pipeline_id=task.id, task=task)
    except Exception as exc:  # noqa: BLE001 — 补救失败如实上报，不吞
        emit(task.id, "degradation", f"Kernel 补派 {stage} 失败：{type(exc).__name__}",
             {"kind": "kernel_recovery_failed", "stage": stage,
              "error": str(exc)[:200]}, span="kernel")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    task.usage.tool_calls += 1
    _collect_degradations(task, "delegate_agent", result)
    if str(result.get("status") or "") == "ok" and not result.get("replayed"):
        _mark_stage_remediated(task.id, stage)
    emit(task.id, "tool_result",
         f"delegate_agent({stage}) → {result.get('status')}",
         {"tool": "delegate_agent", "stage": stage, "status": result.get("status"),
          "replayed": bool(result.get("replayed")), "source": "kernel"},
         span="tool")
    return result


def _pending_degradation_hint(task_id: str) -> str:
    """把待处置降级压成给模型看的短清单（守门提示词用）。"""
    items = _PENDING_DEGRADATIONS.get(task_id) or []
    if not items:
        return ""
    lines = [
        f"- [{d.get('kind')}] {str(d.get('detail') or '')[:200]}"
        + (f"（scope={d.get('scope')}）" if d.get("scope") else "")
        for d in items[:8]
    ]
    more = f"\n（另有 {len(items) - 8} 项同类降级）" if len(items) > 8 else ""
    return (_DEGRADATION_DECISION_HINT_HEAD + "\n".join(lines) + more
            + _DEGRADATION_DECISION_HINT_TAIL)


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
        max_wall_sec=float(getattr(settings, "agent_max_wall_sec", 0.0) or 0.0),
        budget_usd=getattr(settings, "pipeline_budget_usd", None),
    )
    limits.validate_or_raise()
    return limits


def _escrow_of(task: Any) -> Any:
    """取任务的预算托管；没有则 None（测试桩常见）。"""
    try:
        return getattr(task, "escrow", None)
    except Exception:  # noqa: BLE001
        return None


def _goal_of(task: Any) -> Any:
    """取任务目标；缺失时即时创建并尽力挂回（同 `_closeout_of` 的理由）。"""
    goal = getattr(task, "goal", None)
    if goal is not None:
        return goal
    from clipwright.schema.task import Goal

    plan_only = bool(getattr(getattr(task, "request", None), "plan_only", False))
    goal = Goal(
        id=f"goal_{getattr(task, 'id', 'task')}",
        title=("在收口规范内完成规划并交付规划书" if plan_only
               else "在收口规范内完成创作并交付收尾报告"),
    )
    try:
        task.goal = goal  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return goal


def _closeout_of(task: Any) -> Any:
    """取任务的收口状态；**不假设** `task.closeout` 一定存在。

    为什么需要它：`Closeout` 是 2026-11-02 引入的字段，而大量单测用
    `types.SimpleNamespace` / 轻量 dataclass 构造 Task 桩（历史遗留）。在此之前
    Kernel 直接 `task.closeout`，于是这些桩一跑就 `AttributeError`——测试红在
    "桩缺字段"而不是被测逻辑上。真实 `Task` 永远有该字段（default_factory），
    所以这里只是容错：缺失时即时创建一个并尽力挂回任务对象。
    """
    closeout = getattr(task, "closeout", None)
    if closeout is not None:
        return closeout
    from clipwright.schema.task import Closeout

    closeout = Closeout()
    try:
        task.closeout = closeout  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — frozen/slots 桩挂不回去也无妨
        pass
    return closeout


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

    def create(self, request: TaskRequest, *, mode: str = "on",
               limits: Any = None) -> Task:
        """建任务。`limits` 可覆盖四道闸（入口来自 `/proceed` 这类"知道片子多大"的调用方）。

        终止机制快照（devplan 2026-11-02 §6/§7）：创建时按「项目配置 > 全局 settings >
        默认」解析一次策略，把软着陆轮次写进 `Goal.max_rounds`、把质量预算预留冻结成
        `BudgetEscrow`。**只在这里取值** —— 运行中改配置不影响在跑任务，否则同一个任务
        会出现两套阈值，收口线与预算线互相打脸。
        """
        _limits = limits if limits is not None else limits_from_settings()
        task = Task(
            id=f"task_{uuid.uuid4().hex[:10]}",
            request=request,
            mode=mode,
            limits=_limits,
        )
        try:
            self._snapshot_termination_policy(task)
        except Exception as exc:  # noqa: BLE001 — 快照失败绝不阻断建任务
            logger.warning("任务 %s 终止策略快照失败: %s", task.id, exc)
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

    def _snapshot_termination_policy(self, task: Task) -> None:
        """把「软着陆轮次 / 收口窗口 / 质量预算预留」冻结到任务上（devplan §6/§7）。

        为什么快照而不是每次闸门现读：阈值在任务生命期内必须是常量。否则用户在跑
        到一半时改项目配置，收口线会中途移动，日志里也说不清"这一次到底按哪套跑"。
        """
        from clipwright.services import agent_policy as _policy
        from clipwright.schema.task import Goal

        policy = _policy.resolve_for_task(task)
        limit = task.limits
        goal = _goal_of(task)
        goal.max_rounds = int(policy.soft_landing_round)
        goal.blocked_after_consecutive_rounds = int(
            getattr(settings, "agent_goal_blocked_after_rounds", 3) or 3)
        _closeout_of(task).max_rounds = int(policy.closeout_rounds)
        _closeout_of(task).max_tool_calls = int(policy.closeout_tool_calls)
        _closeout_of(task).max_wall_sec = float(policy.closeout_max_wall_sec)
        # 质量预留只在金额预算非空时冻结（不凭空的限制）
        try:
            task.escrow = _policy.build_escrow(limit.budget_usd, policy)
        except AttributeError:  # 冻结/slots 的测试桩
            pass
        logger.info(
            "任务 %s 终止策略快照: source=%s soft_landing_round=%s budget=%s reserve=%s",
            task.id, policy.source, _goal_of(task).max_rounds, limit.budget_usd,
            _escrow_of(task).reserve_usd if _escrow_of(task) else 0.0,
        )
        from clipwright.services.task_events import emit

        emit(task.id, "goal_update", f"目标已建立：{_goal_of(task).title}",
             {"goal_id": _goal_of(task).id, "status": _goal_status_value(task),
              "rounds_used": _goal_of(task).rounds_used, "max_rounds": _goal_of(task).max_rounds,
              "blocker_streak": 0, "blocked_reason": "", "terminal_reason": None,
              "policy_source": policy.source,
              "reserve_usd": _escrow_of(task).reserve_usd if _escrow_of(task) else 0.0},
             span="kernel")

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
        # M3：取消该任务关联的插件 job（状态先落 cancelled；进程终止异步投递，
        # 失败只告警，不阻断任务取消）
        try:
            from clipwright.services.plugin_rpc import job_manager

            job_manager().cancel_for_task(task_id)
        except Exception as exc:  # noqa: BLE001 — 清理失败不影响取消
            logger.warning("取消任务关联插件 job 失败 %s: %s", task_id, exc)
        # devplan T3.1：用户中止也要写机器可读原因（原来只有一个 `"cancelled"` 字符串）。
        # 旧 `reason` 字段照旧产出（用户裁定 4：暂不停用），新代码读 terminal_reason。
        try:
            from clipwright.schema.task import TaskFinish, TerminalReason

            task.terminal_reason = TerminalReason.user_cancelled
            finish = task.finish or TaskFinish()
            finish.reason = finish.reason or "cancelled"
            finish.terminal_reason = TerminalReason.user_cancelled
            finish.conclusion = finish.conclusion or "用户点了停止"
            task.finish = finish
        except Exception as exc:  # noqa: BLE001 — 打标记失败不阻断取消
            logger.warning("取消任务时写 terminal_reason 失败 %s: %s", task_id, exc)
        task.touch()
        # SP15.3：取消任务时作废待答问题（answer 之后返回 409），
        # 并把仍在等待的协程唤醒为取消，避免工具轮永久挂住。
        try:
            from clipwright.services import ask_user as _ask_user

            _ask_user.cancel_owner("task", task_id, reason="cancelled")
        except Exception as exc:  # noqa: BLE001 — 清理失败不影响取消
            logger.warning("取消任务时清理待答问题失败 %s: %s", task_id, exc)
        emit(task_id, "task_end", "任务被取消",
             {"reason": "cancelled", "completed": [], "unfinished": ["用户取消"],
              # devplan §4：新字段与旧 field 并行；旧前端只读 reason 不受影响
              "terminal_reason": "user_cancelled",
              "goal_status": _goal_status_value(task) or None,
              "goal_id": _goal_of(task).id if _goal_of(task) else "",
              "closeout_mode": _closeout_of(task).mode,
              "escrow": _escrow_of(task).model_dump_public() if _escrow_of(task) else None})
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

    # ── plan_only：主 agent 派发"生成制作规划书"的收尾（PRD plan-dispatch D1/D2）──

    async def _finish_plan_only(self, task: Task) -> str:
        """plan_only 任务的守门：确保有分镜场景，并把 `reports/plan.json` 落盘。

        返回 `gate_reason`：`plan_only_completed` / `plan_only_scenes_missing` /
        `plan_only_artifact_missing`。**不**要求 final_timeline——出片是用户点
        「确认实施」之后的事，拿出片标准要求规划书任务会把它一路推到素材/剪辑阶段。
        """
        from clipwright.services import task_blackboard as _bb
        from clipwright.services.task_events import emit

        board = _bb.get(task.id) or {}
        scenes = board.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            # 模型没派结构阶段 → 内核确定性补派一次（与模型派发同一入口，
            # 同样计 usage / 同样进事件流），不让"模型没照做"变成静默失败。
            recovered = int(_KERNEL_RECOVERY.get(task.id, 0))
            if recovered < _MAX_KERNEL_RECOVERY:
                _KERNEL_RECOVERY[task.id] = recovered + 1
                rec = await _kernel_recover_stage(task, "structure",
                                                  "plan_only：缺少分镜场景")
                if str(rec.get("status") or "") in ("ok", "dry_run"):
                    board = _bb.get(task.id) or {}
                    scenes = board.get("scenes")
            if not isinstance(scenes, list) or not scenes:
                emit(task.id, "degradation",
                     "plan_only 没有产出分镜场景，规划书无法生成",
                     {"kind": "plan_only_scenes_missing"}, span="kernel")
                return "plan_only_scenes_missing"
        try:
            payload = self._write_plan_artifact(task, scenes)
        except Exception as exc:  # noqa: BLE001 — 落盘失败如实上报，不假装成功
            emit(task.id, "degradation",
                 f"reports/plan.json 写入失败：{type(exc).__name__}",
                 {"kind": "plan_only_artifact_failed", "error": str(exc)[:200]}, span="kernel")
            return "plan_only_artifact_missing"
        emit(task.id, "artifact",
             f"规划书已落盘：{payload.get('path')}",
             {"kind": "plan", "path": payload.get("path"),
              "scene_count": payload.get("scene_count"),
              "session_id": payload.get("session_id"),
              "brief_hash": payload.get("brief_hash")},
             span="kernel")
        return "plan_only_completed"

    def _write_plan_artifact(self, task: Task, scenes: list[Any]) -> dict[str, Any]:
        """把规划书产物写到工作区 `reports/plan.json`（原子替换）。

        契约见 PRD plan-dispatch §4.3：`brief_hash` 必须与需求侧同源，
        否则"这份规划书对不对应当前简报"无法判定。
        """
        import json as _json
        import os
        from pathlib import Path

        extra = dict(getattr(task.request, "extra", None) or {})
        root = str(extra.get("workspace_root") or "").strip()
        if not root:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(task.id))
            root = str(ws.dirs().root)
        reports = Path(root) / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": str(extra.get("session_id") or ""),
            "brief_hash": str(extra.get("brief_hash") or ""),
            "scenes": scenes,
            "scene_count": len(scenes),
            "summary": f"{len(scenes)} 个场景",
            "generated_by": "kernel",
            "run_id": str(extra.get("run_id") or task.id),
            "task_id": str(task.id),
        }
        path = reports / "plan.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        payload["path"] = str(path)
        return payload

    async def run(self, task: Task) -> Task:
        from clipwright.services.task_events import emit

        started = time.monotonic()
        # 真取消（PRD §18.10.4 / S5）：kernel 自己持有执行句柄，
        # 旧实现句柄留在 api 层，取消只置标记 → 执行中的 900s 阶段停不住。
        _TASK_HANDLES[task.id] = asyncio.current_task()  # type: ignore[assignment]
        # 进度播报（PRD §14）：开工。分母用报告里出现过的阶段数 / 六阶段计划；
        # 拿不到就写"总数未定"，不编百分比。
        try:
            from clipwright.services import progress as _progress

            _progress.emit_progress(
                task.id, trigger="start", action=f"开工：{task.request.topic[:60]}",
                conclusion="开始执行", next_action="先摸清现状再动手",
                budget=_progress.budget_ratio(task, "turns")[1],
                once_key="start")
        except Exception as exc:  # noqa: BLE001 — 播报失败不影响执行
            logger.warning("进度播报（开工）失败 %s: %s", task.id, exc)
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
                 {"reason": task.finish.reason,
                  "completed": [], "unfinished": list(task.finish.unfinished or [])})
            self._checkpoint(task)
            return task
        return await self._run_tool_loop(task, started=started,
                                        shadow=(mode == "shadow"))

    async def _run_tool_loop(self, task: Task, *, started: float,
                             shadow: bool = False, extra_hint: str = "") -> Task:
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
                  # B12/N5：`trimmed` 是**被裁掉的段名 list**，不是布尔。旧实现传 bool，
                  # 前端永远看不到"裁掉了哪一段"。
                  "trimmed": loaded.get("trimmed", []),
                  "budget": loaded.get("budget", 0), "phase": "run"})
        except Exception as exc:  # noqa: BLE001 — 上下文失败不能阻断主循环
            logger.warning("上下文装载失败 %s: %s", task.id, exc)
        if extra_hint:
            context_text = f"{context_text}{extra_hint}"

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
            # ── 质量预算托管结算（devplan §4.2）：唯一汇总入口就在这里 ──
            # 为什么只能在这记：`stage_subagent` 也会产出阶段级 cost_usd，若两边都往
            # 质量账上加，一次质量阶段会被算两遍，主 agent 的可用额度会凭空缩水。
            self._settle_quality_usage(task, name=name, args=args, cost=cost)
            _record_digest(task, name, result)
            # 跨阶段重复动作统计（供 `_gate_hit` 的熔断判据）：
            # 同一工具+同一参数的失败次数在**整个任务**内累计（agent_loop 的 fail_streak
            # 只管单次 run，阶段之间一旦重派就清空，挡不住"每轮都重试同一枪"）。
            try:
                import json as _json_sig

                _fp = f"{name}::{_json_sig.dumps(args or {}, sort_keys=True, ensure_ascii=False)[:200]}"
                _bucket = _REPEAT_FAILS.setdefault(task.id, {})
                if str(result.get("status") or "") in ("ok", "success", "dry_run"):
                    _bucket.pop(_fp, None)
                else:
                    _bucket[_fp] = int(_bucket.get(_fp, 0)) + 1
                    if _bucket[_fp] == _REPEAT_FAIL_LIMIT:
                        emit(task.id, "degradation",
                             f"同一动作连续失败 {_REPEAT_FAIL_LIMIT} 次：{name}（跨阶段计数）",
                             {"tool": name, "repeat_fails": _bucket[_fp], "phase": "repeat_breaker"},
                             span="kernel")
            except Exception:  # noqa: BLE001 — 统计失败不影响执行
                pass
            # 进度播报（PRD §14）：任一预算维度首次过半 → 提醒收紧收尾
            try:
                from clipwright.services import progress as _progress

                _progress.on_budget_half(task)
            except Exception:  # noqa: BLE001 — 播报失败不影响工具循环
                pass
            # 降级信号收集（E2E 2026-10-29）：delegate_agent 的 output 里带
            # degradations 时记成"待处置"，收尾守门会据此强推一轮决策。
            _collect_degradations(task, name, result)
            # 重派同阶段并成功 → 给该阶段降级写 `remediated` 收据（PRD P0）。
            # 但**回放不算修复**：幂等命中只是把旧结果再递一次，字节都没变
            # （E2E 2026-10-30 实测：重派"成功"但 sha256 与降级那次完全相同）。
            if name == "delegate_agent" and str(result.get("status") or "") == "ok":
                try:
                    _stage = str((dict(args or {}).get("stage") or "")).strip()
                except Exception:  # noqa: BLE001
                    _stage = ""
                if result.get("replayed"):
                    emit(task.id, "degradation",
                         f"{_stage or '阶段'} 的重派命中了幂等回放，不算修复",
                         {"stage": _stage, "mode": "replayed_not_fixed"},
                         span="kernel")
                else:
                    _mark_stage_remediated(task.id, _stage)
            # 录制（A2 回放的数据来源）：M3 之后没有步骤级执行，录制点在这里
            from clipwright.services.recording import maybe_record

            maybe_record("tool", name, dict(args or result.get("args") or {}), result,
                         error=result.get("error"))
            # 时间轴实时同步（用户 2026-10-31）：每派完一个阶段就把当前时间线增量写回项目，
            # 编辑器轮询到即时刷新。旧实现只在任务收尾写一次，用户跑片半小时看到的是空时间线。
            # 降级(degraded)也算"阶段交付了一份时间线"：以前只认 ok/dry_run，
            # 于是动画阶段降级时编辑器看不到它的产物变化（用户 2026-11-02：
            # "各个 agent 应该实时生成时间轴，时间轴也会动态更新"）。
            if name == "delegate_agent" and str(result.get("status") or "") in (
                    "ok", "dry_run", "degraded"):
                try:
                    from clipwright.services import task_project_sync as _sync

                    _sync.sync_timeline_to_project(task.id, task, incremental=True)
                except Exception as exc:  # noqa: BLE001 — 同步失败不影响执行
                    logger.warning("阶段后增量回写时间线失败 %s: %s", task.id, exc)
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
        _closeout_flag = str(_closeout_of(task).mode) == "soft_landing"
        try:
            outcome = await driver(
                task, context_text=context_text, shadow=shadow,
                guard=_guard, on_tool_call=_on_tool_call, dry_run=dry_run,
                closeout=_closeout_flag,
            )
        except TypeError:
            # 自定义 driver 不支持 closeout 形参 → 退回旧调用（工具面由 prompt 侧兜底）
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
            # 触闸 → 收尾（devplan §5：先分类再落终态；收口里结束 = capped 而非 failed）
            gate_reason = f"gate:{text}"
            _status, _treason, _blocker = self._classify_gate(task, text)
            task.blocker_code = _blocker
            if _treason == "capped":
                # 收口结束必须有**可交接**的清单：先写报告，再用它的结果填 finish
                payload = self._write_closeout_report(task, why=text)
                completed = list(payload.get("completed") or [])
                unfinished = list(payload.get("unfinished") or [])
                conclusion = ("已跑满运行上限，进入收口并交付了收尾报告"
                              "（这不等于任务全部完成）")
                if getattr(task, 'goal', None) is not None:
                    _goal_of(task).status = GoalStatus.capped
            else:
                completed = []
                unfinished = [text]
                conclusion = f"触闸收尾：{text}"
                if _goal_of(task) is not None and _treason == "blocked":
                    _goal_of(task).status = GoalStatus.blocked
                    _goal_of(task).blocked_reason = str(text)[:500]
                    _goal_of(task).blocker_streak += 1
            task.finish = TaskFinish(
                conclusion=conclusion,
                completed=completed,
                unfinished=unfinished,
                reason=gate_reason,
                terminal_reason=TerminalReason(_treason),
            )
            # T3.5 回滚开关：关掉 agent_terminal_reason_v1 就只写旧 reason 字符串
            if bool(getattr(settings, "agent_terminal_reason_v1", True)):
                task.terminal_reason = TerminalReason(_treason)
            task.status = _status
            task.touch()
            emit(task.id, "task_end", task.finish.conclusion,
                 {"reason": gate_reason, "usage": task.usage.model_dump(),
                  "completed": list(task.finish.completed or []),
                  "unfinished": list(task.finish.unfinished or []),
                  # ── 终止机制（devplan §4：新字段与旧 reason 并行）──
                  "terminal_reason": _treason,
                  "blocker_code": _blocker,
                  "goal_id": _goal_of(task).id if _goal_of(task) else "",
                  "goal_status": _goal_status_value(task) or None,
                  "closeout_mode": _closeout_of(task).mode,
                  "closeout": _closeout_of(task).model_dump(mode="json"),
                  "escrow": _escrow_of(task).model_dump_public() if _escrow_of(task) else None},
                 span="kernel")
            self._emit_goal_update(task, task.finish.conclusion)
            self._record_task_metrics(task, gate_reason)
            self._checkpoint(task)
            return task

        finish = outcome.get("finish") or {}
        plan_only = bool(getattr(getattr(task, "request", None), "plan_only", False))
        if status == "ok":
            conclusion = str(finish.get("conclusion") or "")
            # 收尾守门（§18.10.4）：声称完成但黑板里没有可渲染时间线 → 如实标失败
            gate_reason = "dry_run_preview" if dry_run else "tool_loop_completed"
            # plan_only（主 agent 派发规划书，PRD plan-dispatch D1/D2）：守门换成
            # "有没有分镜场景 + reports/plan.json 落盘"，**不要求 final_timeline**——
            # 出片是用户点「确认实施」之后的事。
            if plan_only and not shadow and not dry_run:
                gate_reason = await self._finish_plan_only(task)
            if not shadow and not dry_run and not plan_only:
                try:
                    from clipwright.services import task_blackboard as _bb

                    board = _bb.get(task.id)
                    # E2E 2026-10-28：`timeline` 只是 edit 的中间产物——audio 阶段才
                    # 产出 `final_timeline`（并把配音/字幕对齐定稿）。旧判据用
                    # `timeline or final_timeline`，于是模型派完 edit 就收尾也算
                    # "完成"：成片渲出来**没有声音**（音频轨 43 个 clip 全是空
                    # asset_id）。守门必须只认 final_timeline。
                    if not board.get("final_timeline"):
                        # 模型提前收尾（有产物但没时间线）→ 带指令再推它一把，
                        # 出不来才判失败。见 _MAX_TIMELINE_CONTINUATIONS 的注释。
                        done = int(_TIMELINE_CONTINUATIONS.get(task.id, 0))
                        if done < _MAX_TIMELINE_CONTINUATIONS:
                            _TIMELINE_CONTINUATIONS[task.id] = done + 1
                            emit(task.id, "narration",
                                 f"收尾守门：黑板里还没有 final_timeline，继续推进"
                                 f"（第 {done + 1}/{_MAX_TIMELINE_CONTINUATIONS} 次）",
                                 {"kind": "timeline_gate_continue",
                                  "attempt": done + 1}, span="kernel")
                            return await self._run_tool_loop(
                                task, started=started, shadow=shadow,
                                extra_hint=_TIMELINE_CONTINUE_HINT,
                            )
                        # 提示词推不动 → Kernel 自己补派（缺什么派什么），再跑一轮。
                        recovered = int(_KERNEL_RECOVERY.get(task.id, 0))
                        if recovered < _MAX_KERNEL_RECOVERY:
                            _KERNEL_RECOVERY[task.id] = recovered + 1
                            _stage = "audio" if board.get("timeline") else "edit"
                            _rec = await _kernel_recover_stage(
                                task, _stage,
                                f"final_timeline 缺失（第 {recovered + 1} 次补派）")
                            if str(_rec.get("status") or "") in ("ok", "dry_run"):
                                return await self._run_tool_loop(
                                    task, started=started, shadow=shadow,
                                    extra_hint=_TIMELINE_CONTINUE_HINT,
                                )
                            # 补派没成（关闭/失败）→ 不再无谓重跑，直接如实收尾
                            gate_reason = (f"缺少可渲染时间线（补派 {_stage} 未成功："
                                           f"{_rec.get('error') or _rec.get('status')}）")
                        else:
                            gate_reason = "缺少可渲染时间线（final_timeline 为空）"
                        gate_reason = "缺少可渲染时间线（final_timeline 为空）"
                    else:
                        _TIMELINE_CONTINUATIONS.pop(task.id, None)
                        # 注意：不要在这里清 `_KERNEL_RECOVERY`——循环会反复进这段
                        # （有 final_timeline 就走 else），清了等于把补派预算无限续，
                        # 造成 gate→补派→gate 的无限递归（实测 RecursionError）。
                        # 它只在任务真正收尾时清。
                except Exception:  # noqa: BLE001 — 守门失败不阻断收尾
                    pass

            # 降级处置收据（PRD modern-agent-review P0）：待处置降级必须留有收据——
            # `remediated`（重派同阶段成功）或 `accepted`（显式调用
            # acknowledge_degradation）。守门只推一次；推完仍无收据 → 按策略判失败，
            # 不再"模型口头说一句接受就 done"。
            #
            # plan_only 例外（PRD plan-dispatch D2）：规划书任务的完成标准是**产物**——
            # `reports/plan.json` 已落盘就说明用户拿到了规划书。分镜走了模板兜底属于
            # "内容打了折扣"，这件事已经作为 degradation 条目出现在时间线上（用户看得见），
            # 不该再让"没有收据"把一份可用的规划书判死（E2E 2026-10-31 实测：
            # 产物已生成却被 degradation_unresolved 回退成 brief_ready）。
            unresolved: list[dict[str, Any]] = []
            if not shadow and not dry_run and not (plan_only and gate_reason == "plan_only_completed"):
                try:
                    from clipwright.services import degradation_ledger

                    # 台账是收据来源；内存镜像是它的子集（debug/测试直写）。
                    # 两者取并集并按 (kind, scope) 去重，避免"只写内存"的路径绕过守门。
                    unresolved = list(degradation_ledger.outstanding(task.id))
                    known = {(str(d.get("kind")), str(d.get("scope"))) for d in unresolved}
                    for item in _PENDING_DEGRADATIONS.get(task.id) or []:
                        key = (str(item.get("kind")), str(item.get("scope")))
                        if key not in known and degradation_ledger.needs_receipt(item):
                            unresolved.append(item)
                            known.add(key)
                except Exception:  # noqa: BLE001 — 台账不可用时退回内存
                    unresolved = list(_PENDING_DEGRADATIONS.get(task.id) or [])
                if unresolved:
                    done = int(_DEGRADATION_CONTINUATIONS.get(task.id, 0))
                    if done < _MAX_DEGRADATION_CONTINUATIONS:
                        _DEGRADATION_CONTINUATIONS[task.id] = done + 1
                        emit(task.id, "narration",
                             "降级决策守门：本轮有未处置降级，要求主 Agent 当轮决策"
                             f"（第 {done + 1}/{_MAX_DEGRADATION_CONTINUATIONS} 次）",
                             {"kind": "degradation_gate_continue",
                              "count": len(unresolved),
                              "kinds": sorted({str(d.get("kind")) for d in unresolved})},
                             span="kernel")
                        return await self._run_tool_loop(
                            task, started=started, shadow=shadow,
                            extra_hint=_pending_degradation_hint(task.id),
                        )
                    # 推完仍无收据：再给 Kernel 一次**真重派**的机会——重跑才是修复，
                    # 模型写不写收据不该决定"画面是否被修好"。
                    recoverable = next(
                        (str(d.get("scope") or "") for d in unresolved
                         if str(d.get("scope") or "") in _TIMELINE_STAGE_FOR_RECOVERY), "")
                    recovered = int(_KERNEL_RECOVERY.get(task.id, 0))
                    if recoverable and recovered < _MAX_KERNEL_RECOVERY:
                        _KERNEL_RECOVERY[task.id] = recovered + 1
                        _rec2 = await _kernel_recover_stage(
                            task, recoverable,
                            f"降级待修（第 {recovered + 1} 次补派）")
                        if str(_rec2.get("status") or "") in ("ok", "dry_run"):
                            return await self._run_tool_loop(
                                task, started=started, shadow=shadow,
                                extra_hint=_pending_degradation_hint(task.id),
                            )
                    # 最后通牒：给一次**只写收据**的机会（预算独立于续跑）
                    final_done = int(_FINAL_RECEIPT.get(task.id, 0))
                    if final_done < _MAX_FINAL_RECEIPT and not shadow and not dry_run:
                        _FINAL_RECEIPT[task.id] = final_done + 1
                        _kinds = sorted({str(d.get("kind")) for d in unresolved})
                        emit(task.id, "narration",
                             "降级守门最后一步：要求主 Agent 写收据"
                             f"（{len(unresolved)} 项：{','.join(_kinds)[:120]}）",
                             {"kind": "final_receipt_attempt", "kinds": _kinds},
                             span="kernel")
                        _hint = (_FINAL_RECEIPT_HINT_HEAD
                                 + "\n".join(f"- kind={d.get('kind')} "
                                              f"scope={d.get('scope') or '-'}"
                                              f"（{str(d.get('detail') or '')[:80]}）"
                                              for d in unresolved[:8])
                                 + _FINAL_RECEIPT_HINT_TAIL)
                        return await self._run_tool_loop(
                            task, started=started, shadow=shadow, extra_hint=_hint,
                        )
                    # 推完仍无收据：strict 策略下不允许悄悄 done
                    policy = str(getattr(settings, "agent_degradation_policy",
                                         "require_receipt") or "require_receipt")
                    if policy == "require_receipt":
                        gate_reason = "degradation_unresolved"
                        emit(task.id, "degradation",
                             f"降级未处置（无 remediated/accepted 收据）："
                             f"{len(unresolved)} 项 → 任务判失败",
                             {"kind": "degradation_unresolved", "policy": policy,
                              "items": unresolved[:8]}, span="kernel")
            if not unresolved:
                _DEGRADATION_CONTINUATIONS.pop(task.id, None)
                _FINAL_RECEIPT.pop(task.id, None)
                _KERNEL_RECOVERY.pop(task.id, None)
            # 进度播报（PRD §14）：收尾前——先讲清已完成/未完成，再落终态。
            try:
                from clipwright.services import progress as _progress

                _done, _total = 0, 0
                try:
                    from clipwright.services import stage_subagent as _ss

                    _done, _total = _ss._stage_progress_counts(task.id)  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    pass
                _progress.emit_progress(
                    task.id, trigger="wrapup", completed=_done, total=_total,
                    action="进入收尾校验",
                    conclusion=f"未完成项 {len(unresolved)} 条" if unresolved else "没有待处置项",
                    next_action="落终态并写检查点",
                    decision_required=bool(unresolved),
                    once_key="wrapup")
            except Exception as exc:  # noqa: BLE001
                logger.warning("进度播报（收尾前）失败 %s: %s", task.id, exc)
            # ── 终态原因编码化（devplan §8）：旧 reason 字符串继续产出，新枚举供机器读 ──
            _treason, _blocker = self._success_terminal_reason(
                task, gate_reason=gate_reason, unresolved=unresolved)
            if bool(getattr(settings, "agent_terminal_reason_v1", True)):
                task.terminal_reason = _treason
            task.blocker_code = _blocker
            if getattr(task, 'goal', None) is not None:
                if _treason in (TerminalReason.achieved, TerminalReason.plan_only_completed,
                                TerminalReason.dry_run_completed):
                    _goal_of(task).status = GoalStatus.achieved
                elif _treason is TerminalReason.capped:
                    _goal_of(task).status = GoalStatus.capped
                elif _treason is TerminalReason.blocked:
                    _goal_of(task).status = GoalStatus.blocked
            task.finish = TaskFinish(
                conclusion=conclusion or f"工具循环结束：{len(outcome.get('tool_calls') or [])} 次调用",
                completed=[],
                unfinished=([
                    "degraded:" + ",".join(sorted({
                        str(d.get("kind")) for d in unresolved
                    }))[:200]
                ] if unresolved else []),
                reason=gate_reason,
                terminal_reason=_treason,
            )
            task.status = ("done" if gate_reason in ("tool_loop_completed",
                                                    "dry_run_preview",
                                                    "plan_only_completed") else "failed")
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
                  # 收尾字段必须随终态一起发：前端没有第二条路能拿到它们，
                  # 缺了就只能用空数组覆盖后端刚写好的任务状态。
                  "completed": list(task.finish.completed or []),
                  "unfinished": list(task.finish.unfinished or []),
                  # ── 终止机制（devplan §4）──
                  "terminal_reason": _treason.value,
                  "blocker_code": _blocker,
                  "goal_id": _goal_of(task).id if _goal_of(task) else "",
                  "goal_status": _goal_status_value(task) or None,
                  "closeout_mode": _closeout_of(task).mode,
                  "escrow": _escrow_of(task).model_dump_public() if _escrow_of(task) else None,
                  "tool_calls": len(outcome.get("tool_calls") or [])},
                 run_id=str(finish.get("run_id") or ""), span="kernel")
            self._emit_goal_update(task, task.finish.conclusion)
            self._record_task_metrics(task, gate_reason)
            self._checkpoint(task)
            return task

        failure = outcome.get("failure") or {}
        category = str(failure.get("category") or "")
        task.terminal_reason = TerminalReason.model_error
        task.blocker_code = category or "model_error"
        if _goal_of(task) is not None and _goal_status_value(task) == "active":
            _goal_of(task).status = GoalStatus.paused
        task.finish = TaskFinish(
            conclusion=(f"工具循环失败：{outcome.get('error') or reason}"
                        + (f"（category={category}，"
                           f"retryable={failure.get('retryable') or 'unknown'}）"
                           if category else "")),
            completed=[], unfinished=[f"tool_loop:{reason}"],
            reason=f"tool_loop_error:{reason}",
            terminal_reason=TerminalReason.model_error,
        )
        task.status = "failed"
        task.touch()
        emit(task.id, "task_end", task.finish.conclusion,
             {"reason": task.finish.reason, "failure": failure,
              "completed": [], "unfinished": list(task.finish.unfinished or []),
              "terminal_reason": "model_error",
              "blocker_code": task.blocker_code,
              "goal_id": _goal_of(task).id if _goal_of(task) else "",
              "goal_status": _goal_status_value(task) or None,
              "closeout_mode": _closeout_of(task).mode,
              "escrow": _escrow_of(task).model_dump_public() if _escrow_of(task) else None,
              "usage": task.usage.model_dump()}, span="kernel")
        self._emit_goal_update(task, task.finish.conclusion)
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

    # ── 软着陆 / 收口（devplan 2026-11-02 §6）─────────────────────────────────

    def _closeout_enabled(self) -> bool:
        return bool(getattr(settings, "agent_soft_landing_enabled", True))

    def _main_budget(self, task: Task) -> Optional[float]:
        """主 agent 的可用金额 = 总额 - 质量预留（用户裁定 2）。

        为什么要经过 escrow 而不是直接用 `limits.budget_usd`：质量阶段的钱是**提前
        冻结**的，主 agent 一旦能花到全额，质量阶段就可能永远跑不起来——而那正是
        "预算耗尽就不许影响质量 agent"要防的事。
        """
        escrow = _escrow_of(task)
        if escrow is not None and escrow.state.value != "disabled":
            return escrow.main_budget_usd
        return task.limits.budget_usd

    def _sync_escrow(self, task: Task) -> None:
        """把 usage 拆到 escrow 的两侧（主 agent / 质量），只做记账不新造限制。"""
        escrow = _escrow_of(task)
        if escrow is None:
            return
        quality = float(escrow.quality_spent_usd or 0.0)
        escrow.main_spent_usd = round(max(0.0, float(task.usage.cost_usd) - quality), 6)

    def _enter_closeout(self, task: Task, *, trigger: str, detail: str = "") -> bool:
        """进入收口模式（软着陆）。幂等：已经在收口就返回 False。

        为什么是"进入模式"而不是"直接收尾"：撞上限说明**预算用完了，不代表任务做完了**。
        直接判失败会把已经生成的产物判死，直接判 done 又是撒谎。收口模式给了模型一个
        只读窗口，把"做到了哪、还缺什么"写成可交接的结论，然后以 capped 结束。
        """
        from clipwright.services.task_events import emit

        closeout = _closeout_of(task)
        if str(closeout.mode) == "soft_landing":
            return False
        closeout.mode = "soft_landing"
        closeout.trigger = str(trigger or "")
        closeout.trigger_round = int(task.usage.turns or 0)
        closeout.tool_calls_at_trigger = int(task.usage.tool_calls or 0)
        closeout.rounds_used = 0
        closeout.tool_calls_used = 0
        closeout.started_at = _utcnow()
        task.touch()
        # 收口期要按自己的基线重新数无进展，否则普通阶段攒下的 streak 会立刻触发终止
        _PROGRESS.pop(task.id, None)
        if getattr(task, 'goal', None) is not None:
            _goal_of(task).rounds_used = int(task.usage.turns or 0)
        emit(task.id, "soft_landing", f"进入收口模式（{trigger}）",
             {"trigger": trigger, "detail": detail[:200],
              "rounds_used": closeout.trigger_round,
              "max_rounds": _goal_of(task).max_rounds if _goal_of(task) else 0,
              "tools_policy": "closeout_only",
              "closeout_window": {"rounds": closeout.max_rounds,
                                  "tool_calls": closeout.max_tool_calls,
                                  "wall_sec": closeout.max_wall_sec},
              "goal_id": _goal_of(task).id if _goal_of(task) else ""},
             span="kernel")
        self._emit_goal_update(task, "进入收口模式，必须在窗口内交付收尾报告")
        logger.info("任务 %s 进入收口模式: trigger=%s rounds=%s", task.id, trigger,
                    closeout.trigger_round)
        return True

    def _closeout_hit(self, task: Task, started: float) -> Optional[str]:
        """收口窗口的闸：只算**进收口之后**的增量，撞到就如实结束。"""
        closeout = _closeout_of(task)
        rounds = max(0, int(task.usage.turns or 0) - int(closeout.trigger_round or 0))
        calls = max(0, int(task.usage.tool_calls or 0) - int(closeout.tool_calls_at_trigger or 0))
        closeout.rounds_used = rounds
        closeout.tool_calls_used = calls
        if rounds >= int(closeout.max_rounds or 8):
            return f"收口窗口用尽（{rounds}/{closeout.max_rounds} 轮）"
        if calls >= int(closeout.max_tool_calls or 128):
            return f"收口窗口用尽（{calls}/{closeout.max_tool_calls} 次工具调用）"
        elapsed = 0.0
        if closeout.started_at is not None:
            elapsed = (datetime.now(tz=timezone.utc) - closeout.started_at).total_seconds()
        if elapsed >= float(closeout.max_wall_sec or 600.0):
            return f"收口窗口用尽（{int(elapsed)}s ≥ {int(closeout.max_wall_sec)}s）"
        return None

    def _closeout_evidence(self, task: Task) -> tuple[list[str], list[str]]:
        """从黑板/事件里**确定性**地取出「已完成 / 未完成」，不依赖模型再写一遍。

        为什么必须有这条兜底：模型在收口窗口里可能一次都没回话（provider 报错、轮次耗光）。
        那时如果 `finish.completed/unfinished` 是空数组，前端只能显示"结束了，没有信息"
        ——这正是用户裁定 4 要防的（"保留未完成和已完成清单"）。
        """
        completed: list[str] = []
        unfinished: list[str] = []
        board: dict[str, Any] = {}
        try:
            from clipwright.services import task_blackboard as _bb

            board = _bb.get(task.id) or {}
        except Exception:  # noqa: BLE001 — 黑板不可用时按"什么都没交付"处理
            board = {}
        plan_only = bool(getattr(task.request, "plan_only", False))
        if plan_only:
            path = Path(_workspace_root_of(task)) / "reports" / "plan.json"
            if path.exists():
                completed.append("reports/plan.json")
            else:
                unfinished.append("reports/plan.json（规划书未落盘）")
        if board.get("final_timeline"):
            completed.append("final_timeline")
        elif not plan_only:
            unfinished.append("final_timeline（没有可渲染时间线）")
        for key in ("scenes", "script_skeleton", "candidate_clips", "timeline"):
            if board.get(key):
                completed.append(str(key))
        # 质量阶段有没有真跑过：跑过就引用它的报告，没跑就如实写没跑
        quality_ran = False
        try:
            from clipwright.services import stage_subagent as _ss

            runs = _ss.list_stages(task.id) or []
            quality_ran = any(str((r or {}).get("stage") or "") == "quality" for r in runs)
        except Exception:  # noqa: BLE001 — 阶段表不可用就按"没跑"处理
            quality_ran = False
        if not quality_ran and (board.get("issues") or board.get("redo_agent")):
            # 黑板里有质检产物（issues / redo_agent）= 质检确实执行过（第二判据，
            # 防止阶段表被清理后把"跑过质检"误报成 quality_not_run）
            quality_ran = True
        if quality_ran:
            completed.append("quality")
        elif not plan_only:
            unfinished.append("quality_not_run（质量阶段未执行，收口不补派）")
        try:
            from clipwright.services import degradation_ledger

            outstanding = degradation_ledger.outstanding(task.id)
            if outstanding:
                unfinished.append("degradation_unresolved:" + ",".join(sorted({
                    str(d.get("kind")) for d in outstanding}))[:200])
        except Exception:  # noqa: BLE001 — 台账不可用不算未完成
            pass
        if not completed and not unfinished:
            unfinished.append("没有任何可交付产物")
        return completed, unfinished

    def _write_closeout_report(self, task: Task, *, why: str) -> dict[str, Any]:
        """把收尾报告落到 `reports/agent-closeout.json`（原子替换），返回 payload。

        报告是**交接物**：下一轮续跑的任务读它就知道上一轮卡在哪、还缺什么，不需要
        把整段对话再塞进上下文。
        """
        import json as _json
        import os

        completed, unfinished = self._closeout_evidence(task)
        payload = {
            "task_id": task.id,
            "goal_id": _goal_of(task).id if _goal_of(task) else "",
            "terminal_reason": "capped",
            "why": str(why or "")[:500],
            "completed": completed,
            "unfinished": unfinished,
            "blocker_code": task.blocker_code,
            "usage": task.usage.model_dump(),
            "closeout": _closeout_of(task).model_dump(mode="json"),
            "goal": _goal_of(task).model_dump(mode="json") if _goal_of(task) else None,
            "escrow": _escrow_of(task).model_dump_public() if _escrow_of(task) else None,
            "generated_at": _utcnow().isoformat(),
            "generated_by": "kernel",
        }
        path = Path(_workspace_root_of(task)) / "reports" / "agent-closeout.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
            _closeout_of(task).report_path = str(path)
            _closeout_of(task).deliverable_status = "delivered"
            payload["path"] = str(path)
        except Exception as exc:  # noqa: BLE001 — 落盘失败仍要把清单放进 finish
            logger.warning("收尾报告写入失败 %s: %s", task.id, exc)
            _closeout_of(task).deliverable_status = "partial"
        return payload

    def _emit_goal_update(self, task: Task, note: str = "") -> None:
        """Goal 状态迁移的统一出口（幂等由调用点保证）。"""
        if _goal_of(task) is None:
            return
        from clipwright.services.task_events import emit

        try:
            emit(task.id, "goal_update", note or f"目标状态：{_goal_status_value(task)}",
                 {"goal_id": _goal_of(task).id, "status": _goal_status_value(task),
                  "rounds_used": _goal_of(task).rounds_used,
                  "max_rounds": _goal_of(task).max_rounds,
                  "blocker_streak": _goal_of(task).blocker_streak,
                  "blocked_reason": _goal_of(task).blocked_reason,
                  "terminal_reason": (task.terminal_reason.value
                                      if task.terminal_reason else None)},
                 span="kernel")
        except Exception as exc:  # noqa: BLE001 — 事件失败不影响状态
            logger.warning("goal_update 事件发送失败 %s: %s", task.id, exc)

    def _classify_gate(self, task: Task, text: str) -> tuple[str, str, str]:
        """触闸文本 → (TaskStatus, TerminalReason 值, blocker_code)。

        `_gate_hit` 只负责"该不该停"，这里负责"这次算什么终态"。两者分开的原因是
        同一个字符串在不同阶段含义不同：<收口窗口用尽> 是 capped（已交付），
        <重复动作熔断> 是 blocked（没做成）。
        """
        in_closeout = str(_closeout_of(task).mode) == "soft_landing"
        text = str(text or "")
        if "重复动作熔断" in text:
            return ("failed", "blocked", "repeat_fail")
        if "没有任何新进展" in text:
            return ("failed", "blocked", "no_progress")
        if "收口窗口用尽" in text or in_closeout:
            # 收口里结束 = 有交付的封顶（真交付与否看 deliverable_status）
            status = "capped" if bool(getattr(settings, "agent_capped_status_v1", True)) else "done"
            return (status, "capped", "budget_exhausted")
        if "预算耗尽" in text or "回合数达到上限" in text or "工具调用数达到上限" in text \
                or "墙钟达到上限" in text:
            return ("failed", "blocked", "budget_exhausted")
        return ("failed", "blocked", "invalid_completion")

    def _settle_quality_usage(self, task: Task, *, name: str, args: Any,
                              cost: Any) -> None:
        """质量阶段的费用记到 escrow 的「质量侧」，并在跑完后结算。

        三条纪律（devplan §4.2）：
        1. 只认 `delegate_agent(stage="quality")` 这一次调用，其它阶段不碰质量账；
        2. 质量费用**同时**计入 `usage.cost_usd`（账单真相只有一个总和），
           主 agent 的可用消费用「总和 − 质量」算，绝不重复加；
        3. 结算后 `released_usd` = 预留 − 实花，**不回流主 agent**（回流等于事后
           扩大主 agent 预算，与"提前预留"的初衷相反）。
        """
        escrow = _escrow_of(task)
        if escrow is None or escrow.state.value == "disabled":
            return
        stage = ""
        if isinstance(args, dict):
            stage = str(args.get("stage") or "").strip()
        if stage != "quality":
            return
        from clipwright.tool.surface import DELEGATE_AGENT

        if name != DELEGATE_AGENT:
            return
        escrow.state = EscrowState.in_use
        if isinstance(cost, (int, float)) and float(cost) > 0:
            escrow.quality_spent_usd = round(
                float(escrow.quality_spent_usd) + float(cost), 6)
        # 质量阶段结束即结算：差额释放（不回流），状态置 settled
        spent = float(escrow.quality_spent_usd or 0.0)
        escrow.released_usd = round(max(0.0, float(escrow.reserve_usd) - spent), 6)
        escrow.state = EscrowState.settled
        escrow.main_spent_usd = round(
            max(0.0, float(task.usage.cost_usd) - spent), 6)
        logger.info(
            "任务 %s 质量预算结算: reserve=%.4f 质量实花=%.4f 释放=%.4f 主 agent=%.4f",
            task.id, escrow.reserve_usd, spent, escrow.released_usd,
            escrow.main_spent_usd)

    def _gate_hit(self, task: Task, started: float) -> Optional[str]:
        task.usage.wall_sec = round(time.monotonic() - started, 2)
        limits = task.limits
        self._sync_escrow(task)
        # ① 已经在收口：只算收口窗口（普通预算/无进展都已"用尽"过一遍，不重复判）
        if str(_closeout_of(task).mode) == "soft_landing":
            return self._closeout_hit(task, started)
        # ② 软着陆触发：撞结构上限（600 轮 / 4000 次调用）→ 进收口，而不是判失败
        if self._closeout_enabled():
            goal = _goal_of(task)
            round_cap = int(getattr(goal, "max_rounds", 0) or 0) or int(
                getattr(settings, "agent_soft_landing_round", 600) or 600)
            if int(task.usage.turns or 0) >= round_cap:
                self._enter_closeout(task, trigger="round_threshold",
                                     detail=f"已用 {task.usage.turns} 轮 ≥ {round_cap}")
                return None
            from clipwright.services.agent_loop import STRUCTURAL_MAX_CALLS

            if int(task.usage.tool_calls or 0) >= STRUCTURAL_MAX_CALLS:
                self._enter_closeout(task, trigger="tool_call_cap",
                                     detail=f"已用 {task.usage.tool_calls} 次调用")
                return None
        # 预算/墙钟闸：0 = 不限（2026-11-02 用户裁定：这三道不再作为中止条件）
        if limits.max_turns and task.usage.turns >= limits.max_turns:
            if self._closeout_enabled():
                self._enter_closeout(task, trigger="budget",
                                     detail=f"回合预算 {limits.max_turns} 用尽")
                return None
            return f"回合数达到上限 {limits.max_turns}"
        if limits.max_tool_calls and task.usage.tool_calls >= limits.max_tool_calls:
            if self._closeout_enabled():
                self._enter_closeout(task, trigger="budget",
                                     detail=f"工具调用预算 {limits.max_tool_calls} 用尽")
                return None
            return f"工具调用数达到上限 {limits.max_tool_calls}"
        if limits.max_wall_sec and task.usage.wall_sec >= limits.max_wall_sec:
            if self._closeout_enabled():
                self._enter_closeout(task, trigger="budget",
                                     detail=f"墙钟预算 {limits.max_wall_sec}s 用尽")
                return None
            return f"墙钟达到上限 {limits.max_wall_sec}s"
        # 金额闸：用**扣除质量预留后**的额度判（用户裁定 2）
        main_budget = self._main_budget(task)
        if main_budget is not None and task.usage.cost_usd >= main_budget:
            if self._closeout_enabled():
                self._enter_closeout(
                    task, trigger="budget",
                    detail=(f"主 agent 可用额度用尽（已花 {task.usage.cost_usd:.4f} ≥ "
                            f"{main_budget:.4f}；质量预留 "
                            f"{getattr(_escrow_of(task), 'reserve_usd', 0.0):.4f} 不动）"))
                return None
            return f"预算耗尽（{task.usage.cost_usd:.4f} ≥ {main_budget}）"
        # ③ 跨阶段重复动作熔断（用户选择"扩展"）：同一 (工具+参数指纹) 在整个任务里
        #    累计失败到阈值就收尾。实测「同一段 video_trim 重试 41 次」就属这类空转。
        for fingerprint, fails in list((_REPEAT_FAILS.get(task.id) or {}).items()):
            if fails >= _REPEAT_FAIL_LIMIT:
                return (f"重复动作熔断：{str(fingerprint)[:80]} 已累计失败 {fails} 次"
                        "（跨阶段，不再重试）")
        # ④ 无进展检测（用户选择"新增"，N=6）：进展 = 黑板键变多 / 上下文事实变多 /
        #    工具调用数变多。连续 N 次闸门检查都没有这些变化 → 收尾并如实报告。
        #    比"等到 3600 秒"或"撞回合上限"更接近现代 agent 的终止方式。
        try:
            from clipwright.services import task_blackboard as _bb_gate

            _board_keys = len(_bb_gate.get(task.id) or {})
        except Exception:  # noqa: BLE001 — 黑板不可用就不把"键数"计入进展
            _board_keys = 0
        sig = (_board_keys, len(task.context_digest), task.usage.tool_calls)
        state = _PROGRESS.setdefault(task.id, {"sig": sig, "same": 0})
        if sig == state["sig"]:
            state["same"] += 1
        else:
            state["sig"], state["same"] = sig, 0
        if state["same"] >= _NO_PROGRESS_LIMIT:
            if self._closeout_enabled():
                # 无进展先软着陆：它常常只是"模型在瞎转"，收口反而能逼它写清卡点
                self._enter_closeout(task, trigger="no_progress",
                                     detail=f"连续 {state['same']} 次检查无新进展")
                return None
            return (f"连续 {state['same']} 次检查没有任何新进展"
                    "（黑板/事实/工具调用都没变化）")
        return None

    def _success_terminal_reason(self, task: Task, *, gate_reason: str,
                                 unresolved: list[dict[str, Any]]) -> tuple[TerminalReason, str]:
        """成功/收尾路径 → (TerminalReason, blocker_code)。

        为什么不能只按 `gate_reason` 映射：同一个 `tool_loop_completed` 在"有
        final_timeline"和"缺产物被推回来后勉强收尾"两种情况下含义完全不同，
        前者是 achieved、后者必须是 blocked（否则成功率会被灌水）。
        """
        if unresolved:
            return TerminalReason.blocked, "degradation_unresolved"
        if gate_reason == "plan_only_completed":
            return TerminalReason.plan_only_completed, ""
        if gate_reason == "dry_run_preview":
            return TerminalReason.dry_run_completed, ""
        if gate_reason == "tool_loop_completed":
            return TerminalReason.achieved, ""
        if str(_closeout_of(task).mode) == "soft_landing":
            return TerminalReason.capped, "budget_exhausted"
        if gate_reason.startswith("缺少可渲染时间线"):
            return TerminalReason.invalid_completion, "missing_artifact"
        return TerminalReason.model_error, "unclassified"

    def _reset_task_state(self, task_id: str) -> None:
        """清理按任务维度的闸门状态（收尾/取消时调用，防内存泄漏）。"""
        _PROGRESS.pop(task_id, None)
        _REPEAT_FAILS.pop(task_id, None)


    def _finish(self, task: Task, *, reason: str) -> None:
        """收尾
        self._reset_task_state(task.id)（M3 后不再按 steps 汇总：没有步骤表，只有一次 tool loop 的结果）。

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
                # 用户裁定 3：capped 不计入成功率，但必须**看得见**（不是消失）
                terminal_reason=(task.terminal_reason_of.value
                                 if task.terminal_reason_of is not None else ""),
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

    def seed_blackboard(self, src_task_id: str, dst_task_id: str) -> int:
        """把上一个任务的**黑板数据**继承给新任务（续跑用），返回继承的键数。

        为什么必须有（用户 2026-11-01 实机）：「任务中止 → 再说一句 → 主 agent 继续」
        起的是**新任务**，黑板天然是空的。于是主 agent 看到的是"黑板空了，但磁盘上还有
        上一轮的产物"，只能靠 read_artifact 逐个摸底（而相对路径还会被拒），最后把
        `delegate_agent(stage="edit")` 直接派出去 → `阶段 edit 缺输入：script_skeleton、
        candidate_clips（建议先派 structure）` → edit 永远开不了工。

        做法与 `_restore_state_sidecar` 一致（同一条恢复契约）：
        ① 同进程内优先用内存黑板；② 否则读上一个任务的 sidecar（`_cache/tasks/<id>.json`）；
        ③ 只补**新任务还没有的键**，绝不覆盖。
        """
        src = str(src_task_id or "")
        dst = str(dst_task_id or "")
        if not src or not dst or src == dst:
            return 0
        from clipwright.services import task_blackboard as _bb

        board: dict[str, Any] = {}
        owners: dict[str, str] = {}
        try:
            board = dict(_bb.get(src) or {})
            if board:
                owners = {k: _bb.owner_of(src, k) for k in board}
        except Exception as exc:  # noqa: BLE001 — 内存黑板不可用就退回 sidecar
            logger.warning("继承黑板：读内存失败 %s: %s", src, exc)
        if not board:
            try:
                path = self._state_path(src)
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    board = dict(data.get("blackboard") or {})
                    owners = dict(data.get("blackboard_owners") or {})
            except Exception as exc:  # noqa: BLE001 — sidecar 坏了就不继承
                logger.warning("继承黑板：读 sidecar 失败 %s: %s", src, exc)
        if not board:
            return 0
        seeded = 0
        for key, value in board.items():
            if key in _bb.get(dst):
                continue
            try:
                _bb.put(dst, key, value, by=str(owners.get(key) or "continuation"))
                seeded += 1
            except Exception as exc:  # noqa: BLE001 — 单键失败不影响其它
                logger.warning("继承黑板键失败 %s/%s: %s", dst, key, exc)
        if seeded:
            logger.info("续跑：从任务 %s 继承了 %d 个黑板键 → %s", src, seeded, dst)
        return seeded

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






