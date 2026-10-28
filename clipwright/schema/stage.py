"""阶段子代理契约（PRD docs/master-agent-prd-2026-10-26.md §3.3）。

与 `spawn_subagent`（只读调研员）的区别：阶段子代理**有结构化输入、有产物、有归属**。
它的返回值是 `StageResult`——主 Agent 靠它决定"继续 / 重派 / 换阶段 / 收尾"，
而不是靠一句 2000 字截断文本去猜。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from clipwright.schema.agent import FailureReport

#: 可派发的阶段（requirements 只用于"规划/改写"，不产出时间线）
StageName = Literal[
    "requirements", "structure", "material", "edit", "animation", "audio", "quality",
]

STAGE_NAMES: tuple[str, ...] = (
    "requirements", "structure", "material", "edit", "animation", "audio", "quality",
)

#: 会写时间线的阶段（决定"要不要合并 timeline"与单向覆盖校验）
TIMELINE_STAGES: frozenset[str] = frozenset({"edit", "animation", "audio"})

#: 默认墙钟：阶段是真干活（跑 ffmpeg/LLM/ffprobe），不是问答，不能按 60s 卡
DEFAULT_STAGE_TIMEOUT = 3600.0


class StageSpec(BaseModel):
    """一次阶段派发的规格单。"""

    stage: StageName
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="阶段特定参数（会与黑板数据合并，同名时 args 优先）",
    )
    #: 显式输入（PRD §16.2 / S2）：给了就**不再从黑板取数**——
    #: 数据必须经主 agent 的手。留空才回落到黑板（兼容旧调用，逐步淘汰）。
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="主 agent 显式提供的阶段输入（artifact 引用或内联值）",
    )
    provider: str = Field(default="", description="留空 = 继承当前默认模型")
    model: str = Field(default="", description="留空 = 继承当前默认模型")
    timeout_sec: float = Field(default=DEFAULT_STAGE_TIMEOUT, gt=0)
    max_turns: int = Field(default=8, gt=0)
    dry_run: bool = Field(default=False, description="只装配输入并回报，不真跑")
    merge: bool = Field(default=True, description="跑完是否自动合并进黑板")
    idem_key: str = Field(default="", description="幂等键：同键重派复用上次结果")


class StageResult(BaseModel):
    """阶段子代理的返回：产物 + 结论 + 降级 + 成本 + 失败分类。"""

    stage: str
    status: Literal["ok", "degraded", "failed", "refused", "cancelled", "dry_run"]
    #: 产物：`{"timeline": {...}}` 或 `{"timeline": {"path": ..., "sha256": ...}}`
    artifacts: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    #: 本次写进黑板的键（主 Agent 据此知道"上一步产出了什么"）
    writes: list[str] = Field(default_factory=list)
    degradations: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    redo_agent: str = ""
    #: None = 未知（不许伪造成 0，金额闸对未知成本是盲的）
    cost_usd: Optional[float] = None
    cost_unknown: bool = False
    #: 归因（modern-agent PRD §2.4.2）：阶段内部模型调用用了多少 token、调了哪些工具。
    #: 拿不到就留 0/空——不伪造数字，让上层把这次记成"未知"。
    tokens: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 0
    #: 工作区报告（markdown）的真路径与序号：子 agent 的文字汇报由 runtime 落盘，
    #: 主 agent 通过 read_report 读取；写盘失败时为空字符串（不是 None）。
    report_path: str = ""
    report_seq: int = 0
    #: 实际使用的模型（留空继承默认时也要回填，用户要知道到底跑了谁）
    provider: str = ""
    model: str = ""
    failure: Optional[FailureReport] = None
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "DEFAULT_STAGE_TIMEOUT",
    "STAGE_NAMES",
    "TIMELINE_STAGES",
    "StageName",
    "StageResult",
    "StageSpec",
]
