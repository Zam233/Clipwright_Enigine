"""调度中枢（Supervisor）数据模型 — B1 起逐步落地。

本文件按开发计划 `docs/supervisor-dev-plan-2026-10-17.md` 分批增长：

- B1：`LedgerEntry`（账本条目，F1）
- B2：`Attribution`（归因结论，F2）、`DiagnosisReport`（诊断书，F5）
- B3：`DispatchOrder`（派工单，F4）
- B8/B9：`WorkerSpec` / `SubagentNode`（F10/F12）、`ExecRecord`（F11）

约定：**只放数据契约，不放业务逻辑**（逻辑在 `services/` 与 `supervisor/`）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .agent import Degradation, FailureReport


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LedgerEntry(BaseModel):
    """一个阶段一次尝试的完整记录（PRD F1）。

    账本是「结构化尝试记录」，不是日志流：它要回答「第几次尝试、什么决策、
    降了什么级、失败原因是什么、花了多少钱、用的哪个模型」。
    """

    id: str
    stage: str
    attempt: int = Field(description="该阶段第几次尝试（1 起）")
    decision: str = Field(default="pass", description="pass / fail / cancelled / skipped")
    input_digest: str = Field(default="", description="输入摘要（不含敏感内容）")
    failure: Optional[FailureReport] = Field(default=None, description="执行失败的结构化回报（F9）")
    degradations: list[Degradation] = Field(
        default_factory=list, description="本次尝试中「完成了但打了折扣」的登记",
    )
    warnings: list[str] = Field(default_factory=list)
    model: str = Field(default="", description="本次尝试使用的 provider/model")
    usage: dict[str, Any] = Field(default_factory=dict, description="input/output tokens 等")
    duration_ms: int = Field(default=0)
    ts: datetime = Field(default_factory=_utcnow)


class Attribution(BaseModel):
    """归因结论（PRD F2）——「这次失败该怪谁」。"""

    primary_stage: str = Field(default="", description="主责任阶段；空 = 未能定位")
    upstream_chain: list[str] = Field(default_factory=list, description="连带的上游阶段（按执行序）")
    evidence_refs: list[str] = Field(default_factory=list, description="证据：账本条目 id")
    confidence: str = Field(default="low", description="high / mid / low")
    unrepairable: bool = Field(default=False, description="资源确实不存在等，重试无意义")
    reason: str = Field(default="", description="人话理由（诊断书直接用）")
    source: str = Field(default="rule", description="rule / llm / fallback")


class DiagnosisReport(BaseModel):
    """诊断书（PRD F5）——给人看的一页结论。"""

    conclusion: str = Field(default="")
    problems: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    attempted: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    cost: dict[str, Any] = Field(default_factory=dict)
    mode: str = Field(default="shadow", description="生成时的调度中枢模式（shadow/on）")
    pipeline_id: str = Field(default="")
