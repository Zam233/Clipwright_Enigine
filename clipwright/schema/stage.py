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
    #: 本次**真实执行**的 run_id（重放时与 idem_key 不同）。阶段内工具事件必须挂它，
    #: 否则重放产生的轨迹会被归到上一次 run 上。
    run_id: str = Field(default="", description="本次执行的 run_id（留空回落 idem_key）")
    #: 追踪 id 与尝试号：adapter 透传，让 StageResult 直接成为 AgentResult 时身份完整
    trace_id: str = Field(default="", description="trace_id（透传给 AgentResult）")
    attempt: int = Field(default=1, ge=1, description="本次尝试号")


def make_stage_result(
    *,
    stage: str,
    status: str,
    summary: str = "",
    artifacts: dict[str, Any] | None = None,
    writes: list[str] | None = None,
    degradations: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    redo_agent: str = "",
    redo_hint: str = "",
    needs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    cost_usd: float | None = None,
    cost_unknown: bool = False,
    tokens: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_calls_path: str = "",
    duration_ms: int = 0,
    provider: str = "",
    model: str = "",
    failure: Any = None,
    error: str = "",
    task_id: str = "",
    run_id: str = "",
    trace_id: str = "",
    attempt: int = 1,
    report_path: str = "",
    report_seq: int = 0,
) -> Any:
    """B21b：测试/兼容用的阶段终态构造器（正式路径走 result_assembler）。

    返回 `AgentResult`——旧的 `StageResult` 模型已删除，避免两套终态并存。
    """
    from clipwright.services import result_assembler as _ra

    return _ra.build(
        run_id=run_id or f"{task_id or 'test'}:{stage}", kind="stage", mode="task",
        task_id=task_id, trace_id=trace_id, attempt=attempt,
        stage=stage, role=stage, status=status,  # type: ignore[arg-type]
        summary=summary, outputs=dict(artifacts or {}), writes=list(writes or []),
        needs=list(needs or []), failure=failure, error=error,
        issues=list(issues or []), redo_agent=redo_agent, redo_hint=redo_hint,
        degradations=list(degradations or []), report_path=report_path,
        report_seq=report_seq, tool_calls=list(tool_calls or []),
        tool_calls_path=tool_calls_path, tokens=tokens,
        cost_usd=cost_usd, cost_unknown=cost_unknown, duration_ms=duration_ms,
        provider=provider, model=model, details=dict(details or {}),
    )


__all__ = [
    "DEFAULT_STAGE_TIMEOUT",
    "STAGE_NAMES",
    "TIMELINE_STAGES",
    "StageName",
    "make_stage_result",
    "StageSpec",
]
