"""Pipeline 数据模型 — 工作流执行上下文。

定义一次完整的视频生产管线的状态和执行轨迹。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStep(BaseModel):
    """管线中的单个执行步骤。"""
    agent_name: str
    status: PipelineStatus = PipelineStatus.PENDING
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    duration_ms: Optional[int] = Field(default=None)
    result: Optional[dict[str, Any]] = Field(default=None)
    error: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0)


class PipelineRequest(BaseModel):
    """启动管线执行的请求。"""
    persona_id: str
    category_plugin_id: str
    topic: str
    extra_params: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(default=False, description="仅生成预览，不渲染")
    use_v2: bool = Field(default=False, description="是否使用 v2 动态路由管线")


class PipelineState(BaseModel):
    """管线执行状态 — 完整的执行上下文。"""
    pipeline_id: str
    status: PipelineStatus = PipelineStatus.PENDING
    request: PipelineRequest
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # 执行状态
    current_agent: Optional[str] = Field(default=None)
    steps: list[PipelineStep] = Field(default_factory=list)

    # Agent 间共享数据
    shared_data: dict[str, Any] = Field(default_factory=dict)

    # 最终输出
    output_path: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)

    def add_step(self, agent_name: str) -> PipelineStep:
        step = PipelineStep(agent_name=agent_name)
        self.steps.append(step)
        return step

    def get_step(self, agent_name: str) -> Optional[PipelineStep]:
        for s in self.steps:
            if s.agent_name == agent_name:
                return s
        return None
