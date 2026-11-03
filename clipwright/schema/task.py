"""Task / Plan / Event 契约（路线 C / C2）。

为什么单独一个 schema 文件：任务语义要同时被 **Kernel、事件流、API、前端** 读取，
把它写在某个服务内部会让三处各解释一遍。这里只放数据结构与状态机常量，不放逻辑。

两条纪律：

1. **事件是事实**：任务状态（进行/暂停/结束）都能由事件流推导；`Task.status` 只是缓存。
2. **Step 状态机封闭**：`pending → running → done|failed|skipped`，不允许多头状态，
   否则"续跑"会变成猜谜。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TaskStatus = Literal["pending", "planning", "running", "paused", "waiting_user",
                      "done", "failed", "cancelled",
                      # 2026-11-02 用户裁定（devplan §5 T3.1）：撞结构上限后完成收口交付，
                      # **既不算 done 也不算 failed**，不计入成功率。单独一个状态而不是
                      # `done + success=false`，是因为成功率、会话映射、工具条三处都必须能
                      # 直接查询区分；用 flag 会逼每个消费者同时理解两个字段。
                      "capped"]
StepStatus = Literal["pending", "running", "done", "failed", "skipped"]
CapabilityKind = Literal["tool", "skill", "pipeline", "subagent", "meta"]

TASK_TERMINAL: frozenset[str] = frozenset({"done", "failed", "cancelled", "capped"})
STEP_TERMINAL: frozenset[str] = frozenset({"done", "failed", "skipped"})

#: 事件类型（PRD F4 固定集合，新增类型必须同步前端 union 与回放器）
EVENT_TYPES: tuple[str, ...] = (
    "task_start",
    "plan",
    "replan",
    "step_start",
    "tool_call",
    "tool_result",
    "approval_request",
    "approval_result",
    "degradation",
    "artifact",
    "step_end",
    "gate_hit",
    "checkpoint",
    "task_end",
    "inject",
    # ── 主 agent 化（PRD §16.3/§16.4/§17.11）：叙述、意图、思考、汇报、文档、上下文 ──
    "narration",       # "我打算这么做，因为…"（规划后 / 改计划后）
    "intent",          # 每次 delegate/工具执行**之前**的一句话（步骤要说的话）
    "thinking",        # provider 的 reasoning_content 增量（无则不发）
    "report",          # 阶段完成后的汇报卡（headline + bullets + artifacts + cost）
    "doc_write",       # 工作区文档写入（docs/*.md、reports/*.md）
    "context_loaded",  # 每跳决策装载了哪些上下文（sources/trimmed/bytes）
    # ── 统一 AgentRun 事件（modern-agent PRD §5.3）：阶段与调研 worker 同一组事件 ──
    "agent_run_start",   # 派出去（spec 摘要）
    "agent_run_update",  # 进度/降级/等审核
    "agent_run_end",     # 收敛（AgentResult 摘要 + 成本）
    # ── 子 agent 透明化（2026-10-29 用户裁定：主 agent 必须看见子 agent 的一切）──
    "subagent_tool",      # 子 agent 的一次工具调用（tool/status/duration/args 摘要）
    "subagent_progress",  # 长阶段的分段进度（动画 3/29、素材 12/43…）
    # ── 反思流 / 主动询问 / 进度播报（PRD §13/§14/§15，2026-10-30）──
    "question",        # agent 主动向用户提问（等回答，任务转 waiting_user）
    "answer",          # 用户/超时默认项回答问题
    "progress",        # Kernel 确定性进度播报（8 类触发点，同时落 reports/progress.jsonl）
    # ── 终止机制收口（devplan 2026-11-02 §4）──
    "goal_update",     # Goal 状态迁移（active/achieved/capped/blocked/paused + 轮次 + blocker 连续数）
    "soft_landing",    # 进入收口模式（一次性，幂等）：触发原因、轮次、工具面策略
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlanStep(BaseModel):
    id: str
    title: str = ""
    capability: str
    kind: CapabilityKind = "tool"
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    est_cost_usd: Optional[float] = None
    status: StepStatus = "pending"
    attempt: int = 0
    result_brief: str = ""
    error: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def terminal(self) -> bool:
        return self.status in STEP_TERMINAL


class Plan(BaseModel):
    """**初始假设**（modern-agent PRD §2.2 / M3）。

    M3 之后它不再是执行游标：Kernel 不会按这份数组逐步执行。
    留着它是为了让前端能显示"当初打算怎么做"，以及给模型当提示词里的参照。
    """

    steps: list[PlanStep] = Field(default_factory=list)
    version: int = 1
    rationale: str = ""
    est_total_cost_usd: Optional[float] = None
    created_at: datetime = Field(default_factory=_utcnow)
    #: 恒为 True：提醒任何读这份数据的人"这是假设，不是执行记录"
    hypothesis_only: bool = True


class TaskEvent(BaseModel):
    seq: int
    type: str
    ts: datetime = Field(default_factory=_utcnow)
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    # ── trace 信封（modern-agent PRD §3.1 / §2.7）──
    # 为什么加在信封层而不是埋进 detail：前端与回放器要能**按 trace 串起一次决策**
    # （模型轮 → 工具调用 → 子 run → 结论），混在 detail 里就只能靠字符串猜。
    trace_id: str = ""
    run_id: str = ""
    span: str = ""              # kernel / model / tool / agent / memory
    causation_id: str = ""      # 触发本条事件的上一跳（run_id 或 event seq）

    def to_public(self) -> dict[str, Any]:
        payload = {
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts.isoformat(),
            "message": self.message,
            "detail": self.detail,
        }
        # 只有真带了才输出：老前端不认识这些键也不会被打扰
        for key in ("trace_id", "run_id", "span", "causation_id"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload


# ── 终止机制契约（docs/agent-termination-devplan-2026-11-02.md §3） ───────────────
# 这一组类型全部是**纯新增**且带默认值：旧 checkpoint（没有这些键）仍能
# `Task.model_validate()` 解析，新 checkpoint 会把它们写出来。


class TerminalReason(str, Enum):
    """任务终态的**唯一机器原因**（devplan §8）。

    为什么要有它：旧实现把原因塞进 `TaskFinish.reason` 自由字符串，前端只能靠
    ``gate:`` / ``tool_loop_error`` 前缀猜，成功率统计也只能比字符串。枚举化之后
    API / 事件 / checkpoint / 统计读同一份真相。`reason` 字符串**继续并行产出**
    （用户裁定 4：暂不停用、不设下线计划），但新代码只读这个字段。
    """

    achieved = "achieved"                              # 完成标准达成（产物齐 + 降级收据已处置）
    plan_only_completed = "plan_only_completed"        # 只要规划书的任务完成
    capped = "capped"                                  # 撞结构上限 → 收口交付（**不等于完成**）
    blocked = "blocked"                                # 同一 blocker 连续 N 轮 → 宣布做不成
    user_cancelled = "user_cancelled"                  # 用户点了停止
    externally_interrupted = "externally_interrupted"  # 服务重启 / 协程丢失
    model_error = "model_error"                        # provider 或内部异常且不可恢复
    invalid_completion = "invalid_completion"          # 声称完成但缺产物，收口恢复也耗尽
    dry_run_completed = "dry_run_completed"            # 预览跑完


class GoalStatus(str, Enum):
    """目标状态机（devplan §7）。`capped` 仍要求交付，`blocked` 才是"做不成"。"""

    active = "active"
    achieved = "achieved"
    capped = "capped"
    blocked = "blocked"
    paused = "paused"


class Goal(BaseModel):
    """任务级「完成约束」（devplan §3 / §7）。

    为什么主 Agent 需要一个 Goal：max_steps 只是**结构上限**，它不能表达"任务到底
    做完了没有"。Goal 承载"在收口规范内把任务真正做完"这个终极目标——它使
    「撞上限」与「做完了」变成两件可分别查询的事，也让 blocker 必须连续 N 轮才认账。
    """

    id: str = ""
    title: str = ""
    completion_criteria: list[str] = Field(default_factory=list)
    max_rounds: int = 600
    rounds_used: int = 0
    status: GoalStatus = GoalStatus.active
    #: 宣告 blocked 时必须写（20–500 字）：阻塞事实 + 已尝试动作 + 解除条件
    blocked_reason: str = ""
    #: 阻塞指纹（sha256(category + 阻塞点 + 缺失产物)）；相同才累计连续轮数
    blocker_fingerprint: str = ""
    blocker_streak: int = 0
    #: 同一条件要连续这么多轮才允许 blocked（DSH 取 3，照抄）
    blocked_after_consecutive_rounds: int = 3
    #: True = 结束前必须交出收尾报告（capped 也必须有内容）
    closeout_required: bool = True
    created_at: datetime = Field(default_factory=_utcnow)

    def touch_round(self) -> None:
        self.rounds_used += 1


class EscrowState(str, Enum):
    """质量预算托管状态（devplan §4.1）。"""

    disabled = "disabled"   # budget_usd=null（金额不限）→ 不冻结，也不凭空造上限
    frozen = "frozen"       # 已冻结，等质量阶段
    in_use = "in_use"       # 质量阶段正在消费预留
    settled = "settled"     # 质量跑完，差额释放
    released = "released"   # 质量未运行/任务中止 → 释放（**不回流主 agent**）


class BudgetEscrow(BaseModel):
    """为质量 agent 预留的预算（用户裁定 2：即便总额耗尽也不许影响质量 agent）。

    为什么是"托管"而不是"判断时把 reserve 减掉"：预算闸会被调用很多次（每轮、每次
    工具前），临时减法会让"质量阶段还能不能跑"随主 agent 花钱而漂移。冻结一份
    快照后，主 agent 的可用额度在任务生命期内是**稳定**的，质量阶段的额度也是。
    """

    state: EscrowState = EscrowState.disabled
    #: 冻结给质量阶段的额度（USD）
    reserve_usd: float = 0.0
    #: 主 agent 可用额度 = budget_usd - reserve_usd（null = 不限）
    main_budget_usd: Optional[float] = None
    main_spent_usd: float = 0.0
    quality_spent_usd: float = 0.0
    #: 未使用而释放的额度（不回流主 agent，只做审计）
    released_usd: float = 0.0
    ratio: float = 0.2
    floor_usd: float = 0.05
    cap_usd: float = 2.0
    frozen_at: Optional[datetime] = None

    @property
    def quality_remaining_usd(self) -> float:
        if self.state == EscrowState.disabled:
            return 0.0
        return max(0.0, round(self.reserve_usd - self.quality_spent_usd, 6))

    def model_dump_public(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["quality_remaining_usd"] = self.quality_remaining_usd
        return payload


class Closeout(BaseModel):
    """收口模式（软着陆）运行状态（devplan §6）。"""

    #: normal（正常执行） / soft_landing（只读 + 写收尾报告）
    mode: Literal["normal", "soft_landing"] = "normal"
    #: 触发源：round_threshold / structural_cap / tool_call_cap / budget / repeat_fail / no_progress
    trigger: str = ""
    trigger_round: int = 0
    #: 进收口那一刻已用掉多少次工具调用（收口窗口按**增量**算，不吃普通预算）
    tool_calls_at_trigger: int = 0
    #: 收口窗口自己的预算（独立于普通轮次，任务创建时按策略快照写入）
    max_rounds: int = 8
    max_tool_calls: int = 128
    max_wall_sec: float = 600.0
    rounds_used: int = 0
    tool_calls_used: int = 0
    #: 收尾交付状态："" 未交付 / delivered 已交付 / partial 部分交付
    deliverable_status: Literal["", "delivered", "partial"] = ""
    report_path: str = ""
    started_at: Optional[datetime] = None


class TaskLimits(BaseModel):
    """四道闸（PRD F5 / Q1 已裁定；2026-11-02 用户裁定墙钟不设上限）。

    墙钟默认 **0 = 不设上限**：一个 40 分钟的长片任务被 3600s 硬切，是"现代 agent"
    最不该有的行为——真正的中止条件应该是「没有进展 / 撞回合或工具预算 / 预算耗尽 /
    降级未处置 / 完成标准达成 / 用户取消」，而不是"到点了"。
    `max_turns` / `max_tool_calls` 仍然有效（它们约束的是**行为量**，不是寿命）。
    """

    #: 2026-11-02 用户裁定：回合 / 工具调用 / 墙钟**都不再是中止条件**（0 = 不限）。
    #: 真正的终止条件改为：无进展检测(N=6) / 跨阶段重复动作熔断 / 降级未处置 /
    #: 完成标准达成 / 用户取消或暂停。想恢复硬预算就显式填值。
    max_turns: int = 0
    max_tool_calls: int = 0
    max_wall_sec: float = 0.0
    budget_usd: Optional[float] = None

    def validate_or_raise(self) -> None:
        for field, value in (
            ("max_turns", self.max_turns),
            ("max_tool_calls", self.max_tool_calls),
            ("max_wall_sec", self.max_wall_sec),
        ):
            # 2026-11-02 用户裁定：三道硬闸改成"0 = 不限"（现代 agent 的终止条件交给无进展/重复动作/降级/完成标准/用户取消）。负数仍视为配置错误。
            if value is None:
                raise ValueError(f"{field} 不可为 null；不限请填 0")
            if float(value) < 0:
                raise ValueError(f"{field} 不可为负（不限请填 0）")
        if self.budget_usd is not None and self.budget_usd <= 0:
            # 金额闸是"未配置=无上限"，但填了 0 是配置错误而不是无上限
            raise ValueError("budget_usd 若填写必须 > 0；不限就留空(null)")


class TaskUsage(BaseModel):
    turns: int = 0
    tool_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    wall_sec: float = 0.0
    #: 成本未知的 costly 调用次数（金额闸对它们无效，必须可见而不是当成 0）
    unknown_cost_calls: int = 0


class TaskFinish(BaseModel):
    """收尾三件套（PRD A1）：结论 + 已完成产物 + 未完成原因。

    用户裁定 3/4（2026-11-02）：`completed` / `unfinished` **永久保留**，capped 终态
    也必须**有内容**（模型收口失败时由 Kernel 确定性 fallback 生成）；`reason` 字符串
    暂不停用，与 `terminal_reason` 并行产出，新代码只读后者。
    """

    conclusion: str = ""
    completed: list[str] = Field(default_factory=list)
    unfinished: list[str] = Field(default_factory=list)
    reason: str = ""
    #: 终态唯一机器原因（旧 checkpoint 无此键 → None，读取层再按 reason 回退）
    terminal_reason: Optional[TerminalReason] = None


class TaskRequest(BaseModel):
    topic: str = Field(description="用户一句话需求")
    persona_id: str = ""
    category_plugin_id: str = ""
    duration_sec: float = 0.0
    plan_only: bool = True     # Q3：默认先给计划看
    extra: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    request: TaskRequest
    status: TaskStatus = "pending"
    mode: str = "on"
    plan: Optional[Plan] = None
    limits: TaskLimits = Field(default_factory=TaskLimits)
    usage: TaskUsage = Field(default_factory=TaskUsage)
    finish: Optional[TaskFinish] = None
    context_digest: list[dict[str, Any]] = Field(default_factory=list)
    #: 人工插话（C5）：进了规划器才算数，出下一版计划后被消费清空
    hints: list[str] = Field(default_factory=list)
    # ── 终止机制（devplan 2026-11-02 §3；全部带默认值，旧 checkpoint 可解析）──
    #: 任务的「完成约束」：撞结构上限 ≠ 做完了，这两件事必须能分开查询
    goal: Optional[Goal] = None
    #: 终态的**唯一机器原因**；None = 尚未结束（或旧任务没写过）
    terminal_reason: Optional[TerminalReason] = None
    #: Goal 被判 blocked 时的根因码（no_progress / repeat_fail / ... ），与 terminal_reason 分工：
    #: terminal_reason 说"这次是怎么结束的"，blocker_code 说"卡在哪一类原因上"
    blocker_code: str = ""
    #: 收口模式运行状态（是否已软着陆、收尾交付到哪一步）
    closeout: Closeout = Field(default_factory=Closeout)
    #: 质量 agent 的预算托管（budget_usd 为 null 时恒为 disabled）
    escrow: Optional[BudgetEscrow] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def touch(self) -> None:
        self.updated_at = _utcnow()

    @property
    def terminal(self) -> bool:
        return self.status in TASK_TERMINAL

    @property
    def terminal_reason_of(self) -> Optional[TerminalReason]:
        """终态原因：优先新字段，旧任务回退 `finish.terminal_reason`。

        为什么要有这条回退：M1 之前创建的任务从没写过 `Task.terminal_reason`，
        但它们的 `finish` 里记录过最后说法；直接读 None 会让"成功率/会话映射"把
        老任务当未知终态处理。
        """
        if self.terminal_reason is not None:
            return self.terminal_reason
        if self.finish is not None:
            return self.finish.terminal_reason
        return None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "mode": self.mode,
            "request": self.request.model_dump(mode="json"),
            "plan": self.plan.model_dump(mode="json") if self.plan else None,
            "limits": self.limits.model_dump(mode="json"),
            "usage": self.usage.model_dump(mode="json"),
            "finish": self.finish.model_dump(mode="json") if self.finish else None,
            "context_digest": self.context_digest,
            "hints": self.hints,
            # ── 终止机制（纯新增键；旧前端不认识也不会被打扰）──
            "goal": self.goal.model_dump(mode="json") if self.goal else None,
            "terminal_reason": (self.terminal_reason_of.value
                                if self.terminal_reason_of is not None else None),
            "blocker_code": self.blocker_code,
            "closeout": self.closeout.model_dump(mode="json"),
            "escrow": self.escrow.model_dump_public() if self.escrow else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
