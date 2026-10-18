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
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TaskStatus = Literal["pending", "planning", "running", "paused", "done", "failed", "cancelled"]
StepStatus = Literal["pending", "running", "done", "failed", "skipped"]
CapabilityKind = Literal["tool", "skill", "pipeline", "subagent", "meta"]

TASK_TERMINAL: frozenset[str] = frozenset({"done", "failed", "cancelled"})
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
    steps: list[PlanStep] = Field(default_factory=list)
    version: int = 1
    rationale: str = ""
    est_total_cost_usd: Optional[float] = None
    created_at: datetime = Field(default_factory=_utcnow)

    def next_step(self) -> Optional[PlanStep]:
        for step in self.steps:
            if step.status == "pending":
                return step
        return None


class TaskEvent(BaseModel):
    seq: int
    type: str
    ts: datetime = Field(default_factory=_utcnow)
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts.isoformat(),
            "message": self.message,
            "detail": self.detail,
        }


class TaskLimits(BaseModel):
    """四道闸（PRD F5 / Q1 已裁定）。前三道不可置空；金额闸未配置=无上限。"""

    max_turns: int = 30
    max_tool_calls: int = 80
    max_wall_sec: float = 3600.0
    budget_usd: Optional[float] = None

    def validate_or_raise(self) -> None:
        for field, value in (
            ("max_turns", self.max_turns),
            ("max_tool_calls", self.max_tool_calls),
            ("max_wall_sec", self.max_wall_sec),
        ):
            if value is None or float(value) <= 0:
                raise ValueError(
                    f"{field} 不可置空（PRD F5：回合/调用数/墙钟三道闸必须有值）"
                )
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
    """收尾三件套（PRD A1）：结论 + 已完成产物 + 未完成原因。"""

    conclusion: str = ""
    completed: list[str] = Field(default_factory=list)
    unfinished: list[str] = Field(default_factory=list)
    reason: str = ""


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
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def touch(self) -> None:
        self.updated_at = _utcnow()

    @property
    def terminal(self) -> bool:
        return self.status in TASK_TERMINAL

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
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
