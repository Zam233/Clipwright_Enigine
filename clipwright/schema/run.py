"""统一 Agent 运行时契约（PRD docs/modern-agent-prd-2026-10-27.md §2.4）。

为什么单独一个 schema 文件：`AgentRun` 要同时被「阶段子 agent」「调研 worker」
「Kernel 的 delegate_agent 工具」「前端子 agent 卡片」读取。把它们写进任一服务内部，
就会出现"三个地方各解释一遍"——正是这次要消灭的架构债。

三条纪律：

1. **输入显式**：`inputs` 由调用方（主 agent）给出，运行时**不**从黑板/环境里补；
2. **产物引用化**：`outputs` 里放大对象一律是 `{artifact, path, sha256, bytes, preview}`；
3. **文字汇报是一等公民**：`report_path` 指向工作区 markdown（append-only），
   和结构化结果同等重要。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RunKind = Literal["stage", "research"]
RunStatus = Literal["pending", "running", "needs", "ok", "degraded", "failed",
                     "refused", "cancelled"]


class RunSpec(BaseModel):
    """一次子 agent 派发的规格（阶段 / 调研共用）。"""

    kind: RunKind = "stage"
    #: 阶段子 agent 的阶段名（structure/material/...）；调研时可为空
    stage: str = ""
    role: str = ""
    brief: str = ""
    #: 显式输入：artifact 引用或内联小对象（运行时不做隐式补全）
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: 期望产出（字段名 → schema 名），供 CompletionGate/结果校验使用
    expect: dict[str, str] = Field(default_factory=dict)
    #: 允许的工具子集（调研 worker 用；阶段子 agent 用自身能力集）
    tools: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    timeout_sec: float = 900.0
    max_turns: int = 8
    #: 深度：root=0，子 agent 至少 1；>=2 一律拒绝（星型拓扑）
    depth: int = 1
    idem_key: str = ""
    parent_run_id: str = ""
    trace_id: str = ""


class AgentResult(BaseModel):
    """子 agent 的统一返回（阶段与调研同一形状）。"""

    run_id: str
    kind: RunKind
    # ── 归因字段（PRD §2.4.2）：没有它们，"谁花的钱、谁失败的"只能靠猜 ──
    task_id: str = ""
    trace_id: str = ""
    attempt: int = 1
    stage: str = ""
    role: str = ""
    status: RunStatus
    summary: str = ""
    #: 结构化产物（引用或内联小值）
    outputs: dict[str, Any] = Field(default_factory=dict)
    writes: list[str] = Field(default_factory=list)
    needs: list[dict[str, Any]] = Field(default_factory=list)
    #: 工作区 markdown 报告（append-only）
    report_path: str = ""
    report_seq: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)
    iterations: int = 0
    #: 本次 run 实际调用的工具（含成败），主 agent 据此判断"它到底干了什么"
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tokens: int = 0
    cost_usd: Optional[float] = None
    cost_unknown: bool = True
    duration_ms: int = 0
    provider: str = ""
    model: str = ""
    created_at: float = Field(default_factory=time.time)
    failure: Optional[dict[str, Any]] = None
    error: str = ""

    def to_tool_payload(self) -> dict[str, Any]:
        """给模型看的最小形状（工具结果回填用；不含大对象）。"""
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "stage": self.stage,
            "role": self.role,
            "status": self.status,
            "summary": self.summary[:800],
            "outputs": {k: _preview(v) for k, v in list(self.outputs.items())[:8]},
            "writes": self.writes[:12],
            "needs": self.needs[:5],
            "report_path": self.report_path,
            "tool_calls": self.tool_calls[:20],
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "failure": self.failure,
            "error": self.error,
        }


def _preview(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("artifact") or value.get("path"):
            return {"artifact": value.get("artifact", ""),
                    "preview": str(value.get("preview", ""))[:160]}
        return {k: str(value[k])[:60] for k in list(value)[:4]}
    if isinstance(value, list):
        return f"{len(value)} 项"
    return str(value)[:200]


__all__ = ["AgentResult", "RunKind", "RunSpec", "RunStatus"]
