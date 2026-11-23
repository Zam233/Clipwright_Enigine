"""工具层数据模型 — ToolRegistry 和执行结果的标准契约。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    """工具执行状态。"""
    SUCCESS = "success"
    ERROR = "error"
    NOT_FOUND = "not_found"
    DEPENDENCY_MISSING = "dependency_missing"


class ToolExecResult(BaseModel):
    """工具执行结果的标准化格式。"""
    status: ToolStatus = ToolStatus.SUCCESS
    tool_name: str = Field(default="", description="工具名称")
    output: dict[str, Any] = Field(
        default_factory=dict, description="工具输出数据"
    )
    output_path: Optional[str] = Field(
        default=None, description="产物路径（如有）"
    )
    error: Optional[str] = Field(default=None, description="错误信息")
    warning: Optional[str] = Field(default=None, description="警告信息")
    #: 本次调用的成本（美元）。只有工具作者知道真实成本（如视觉模型按次计费）；
    #: 留空表示"未知"——消费方必须记成未知，而不是当成 0（PRD A5 的口径）。
    cost_usd: Optional[float] = Field(
        default=None, description="本工具调用成本（美元）；None = 未知"
    )

    model_config = {"use_enum_values": True}


class ToolInfo(BaseModel):
    """工具的元信息。"""
    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="参数名 → {type, description, required, default}",
    )
    available: bool = Field(
        default=False, description="当前环境是否可用"
    )


class ToolError(BaseModel):
    """统一错误四件套（PRD §6）：调用方按它决定重试/补上游/放弃。"""
    category: str = "unknown"
    blocking_point: str = ""
    retryable: str = "retry_same"
    suggestion: str = ""


class BatchItem(BaseModel):
    """批量工具的单条结果；永不让整批因一项失败而 500。"""
    index: int
    status: Literal["ok", "failed", "skipped"] = "ok"
    error: Optional[str] = None
    error_info: Optional[ToolError] = None
    data: dict[str, Any] = Field(default_factory=dict)


class BatchSummary(BaseModel):
    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0


class JobRef(BaseModel):
    """长任务回执；工具立即返回它，执行进度通过 /api/tool/jobs 查询。"""
    job_id: str
    status: Literal[
        "queued", "running", "done", "partial", "failed", "cancelled"
    ] = "running"
    total: int = 0
    progress: dict[str, int] = Field(default_factory=dict)


class BatchResult(BaseModel):
    """批量工具统一返回形状（PRD §6 工具契约原则）。"""
    job: Optional[JobRef] = None
    status: Literal[
        "queued", "running", "done", "partial", "failed", "cancelled"
    ] = "done"
    items: list[BatchItem] = Field(default_factory=list)
    summary: BatchSummary = Field(default_factory=BatchSummary)
    failures: list[BatchItem] = Field(default_factory=list)

    @classmethod
    def from_items(cls, items: list[BatchItem], *,
                   job: JobRef | None = None) -> "BatchResult":
        ok = sum(1 for i in items if i.status == "ok")
        failed = sum(1 for i in items if i.status == "failed")
        skipped = sum(1 for i in items if i.status == "skipped")
        status = "done" if failed == 0 else ("partial" if ok else "failed")
        return cls(
            job=job, status=status, items=items,
            summary=BatchSummary(total=len(items), ok=ok, failed=failed,
                                 skipped=skipped),
            failures=[i for i in items if i.status == "failed"],
        )
