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
    #: 显式重跑（run 详情里的「重放」/降级补救）：跳过幂等回放，真的再干一次。
    refresh: bool = False


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
    #: 这次派发的原始要求（重放时要"照原样再干一次"，不能只凭 summary 猜）
    brief: str = ""
    #: 结构化产物（引用或内联小值）
    outputs: dict[str, Any] = Field(default_factory=dict)
    writes: list[str] = Field(default_factory=list)
    needs: list[dict[str, Any]] = Field(default_factory=list)
    #: 工作区 markdown 报告（append-only）
    report_path: str = ""
    report_seq: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)
    #: 本步**完成了但打了折扣**（PRD F9 `note_degradation` 的落地）。与 failure 的区别：
    #: failure 是没做完；degradation 是伪装成成功的次品（LLM JSON 截断、自批判被跳过、
    #: 模板兜底、fastpath 回退 Chrome）。主 Agent 必须看见它并当轮决策，
    #: 否则「截断/回退」就被默认接受（E2E 2026-10-29 用户裁定）。
    degradations: list[dict[str, Any]] = Field(default_factory=list)
    iterations: int = 0
    #: 本次 run 实际调用的工具（含成败），主 agent 据此判断"它到底干了什么"
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    #: 这次结果是幂等缓存回放（**没有真的重跑**）。降级修复必须据此判断，
    #: 否则"重派成功"可能只是拿到了同一份旧字节（E2E 2026-10-30 实测踩到）。
    replayed: bool = False
    #: 工具轨迹全量落盘路径（内存只留最近 200 条；主 agent 按此路径取完整 transcript）
    tool_calls_path: str = ""
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
        """给模型看的最小形状（工具结果回填用；不含大对象）。

        现代 agent 化（用户 2026-10-29）：子 agent 不能"偷偷摸摸干活"。这里除了产物，
        还给出**执行轨迹**——调了哪些工具、失败几次、最慢的是哪个、全量在哪取。
        主 agent 的判断依据必须是"它到底干了什么"，而不是一句 summary。
        """
        stats, sample = _tool_call_digest(self.tool_calls)
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "stage": self.stage,
            "role": self.role,
            "status": self.status,
            # 降级信号必须进模型视野：只写日志等于"主 Agent 默许截断/回退"
            "degraded": bool(self.degradations),
            "degradation_count": len(self.degradations),
            "degradations": [
                {k: str(v)[:300] for k, v in dict(d).items()
                 if k in ("kind", "detail", "scope", "impact")}
                for d in self.degradations[:8]
            ],
            "summary": self.summary[:800],
            "outputs": {k: _preview(v) for k, v in list(self.outputs.items())[:8]},
            "writes": self.writes[:12],
            "needs": self.needs[:5],
            "report_path": self.report_path,
            # 轨迹：统计 + 失败优先采样（全量走 get_agent_run，不塞爆上下文）
            "tool_call_stats": stats,
            "tool_calls": sample,
            "tool_calls_total": len(self.tool_calls),
            "tool_calls_path": self.tool_calls_path,
            "tool_calls_hint": (
                f"共 {len(self.tool_calls)} 次工具调用（内存仅保留最近 200 条）；"
                f"完整轨迹见 {self.tool_calls_path or 'get_agent_run'} "
                f"或 get_agent_run(stage=\"{self.stage}\")"
            ) if self.tool_calls else "",
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "failure": self.failure,
            "error": self.error,
        }


def _tool_call_digest(calls: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把工具轨迹压成"主 agent 一眼能判断"的形状：统计 + 失败优先采样。"""
    if not calls:
        return {}, []
    ok_status = ("success", "ok")
    by_status: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    slowest = {"tool": "", "duration_ms": 0}
    for call in calls:
        if not isinstance(call, dict):
            continue
        status = str(call.get("status") or "").lower() or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        tool = str(call.get("tool") or call.get("capability") or "")
        if tool:
            by_tool[tool] = by_tool.get(tool, 0) + 1
        ms = int(call.get("duration_ms") or 0)
        if ms > int(slowest["duration_ms"]):
            slowest = {"tool": tool, "duration_ms": ms}
    failed = sum(n for s, n in by_status.items() if s not in ok_status)
    stats = {
        "total": sum(by_status.values()),
        "failed": failed,
        "by_status": by_status,
        "top_tools": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])[:5]),
        "slowest": slowest,
    }
    # 采样：失败的最该被看见，其次是最慢的，最后补最近的
    bad = [c for c in calls if isinstance(c, dict)
           and str(c.get("status") or "").lower() not in ok_status]
    rest = [c for c in calls if isinstance(c, dict) and c not in bad]
    rest.sort(key=lambda c: -int(c.get("duration_ms") or 0))
    sample: list[dict[str, Any]] = []
    for call in (bad + rest):
        item = {k: call.get(k) for k in
                ("tool", "capability", "status", "duration_ms", "args_brief", "output_brief", "error")}
        if any(v not in (None, "", 0) for v in item.values()):
            sample.append({k: (str(v)[:200] if v is not None else "") for k, v in item.items()})
        if len(sample) >= 12:
            break
    return stats, sample


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
