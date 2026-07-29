"""需求采集相关数据模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class SessionStatus(str, Enum):
    INIT = "init"
    GATHERING = "gathering"
    BRIEF_READY = "brief_ready"
    BRIEF_CONFIRMED = "brief_confirmed"
    PLAN_READY = "plan_ready"
    PLAN_CONFIRMED = "plan_confirmed"
    PIPELINE_RUNNING = "pipeline_running"
    PIPELINE_DONE = "pipeline_done"
    EXPIRED = "expired"


class RequirementsMessage(BaseModel):
    """单条对话消息。"""
    role: MessageRole
    content: str
    timestamp: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreativeBrief(BaseModel):
    """创作方案摘要。"""
    title: str = ""
    overview: str = ""
    target_audience: str = ""
    style_direction: str = ""
    duration_estimate: str = ""
    key_points: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class ProductionPlan(BaseModel):
    """成片规划书。"""
    scene_count: int = 0
    total_duration_sec: float = 0
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    music_suggestion: str = ""
    transition_style: str = ""
