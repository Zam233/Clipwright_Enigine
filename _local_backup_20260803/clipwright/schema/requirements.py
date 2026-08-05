"""需求 Agent Schema — 会话、消息、创作方案、规划书。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """会话状态机。"""
    INIT = "init"                   # 初始状态
    GATHERING = "gathering"         # 收集需求中
    BRIEF_READY = "brief_ready"     # 创作方案已生成，等待确认
    BRIEF_CONFIRMED = "brief_confirmed"  # 方案已确认
    PLANNING = "planning"           # 正在生成规划书
    PLAN_READY = "plan_ready"       # 规划书已生成，等待确认
    PLAN_CONFIRMED = "plan_confirmed"   # 规划书已确认
    PIPELINE_RUNNING = "pipeline_running"  # 正在执行管线
    PIPELINE_DONE = "pipeline_done"      # 管线已完成
    CANCELLED = "cancelled"         # 已取消
    ERROR = "error"                  # 错误


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class RequirementsMessage(BaseModel):
    """单条对话消息。"""
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreativeBrief(BaseModel):
    """创作方案 — 由需求 Agent 生成的整体方案。"""
    title: str = ""
    overview: str = ""               # 方案概述
    target_audience: str = ""        # 目标受众
    core_message: str = ""           # 核心信息
    style_direction: str = ""        # 风格方向
    structure_suggestion: str = ""   # 结构建议
    duration_estimate: str = ""      # 预估时长
    key_elements: list[str] = Field(default_factory=list)  # 关键元素
    special_requirements: list[str] = Field(default_factory=list)  # 特殊要求


class ProductionPlan(BaseModel):
    """成片规划书 — 由结构 Agent 生成、需求 Agent 翻译。"""
    raw_scenes: list[dict[str, Any]] = Field(default_factory=list)  # 结构 Agent 原始输出
    translated_summary: str = ""      # 需求 Agent 翻译后的规划摘要
    total_duration_sec: float = 0     # 总时长
    scene_count: int = 0              # 场景数量
    sections: list[dict[str, Any]] = Field(default_factory=list)  # 分段规划 [{title, scenes, description}]
    markdown_content: str = ""        # 完整的 Markdown 规划书


class RequirementsSession(BaseModel):
    """需求对话会话。"""
    session_id: str = ""
    status: SessionStatus = SessionStatus.INIT
    messages: list[RequirementsMessage] = Field(default_factory=list)
    creative_brief: Optional[CreativeBrief] = None
    production_plan: Optional[ProductionPlan] = None
    pipeline_id: str = ""
    user_inputs: dict[str, Any] = Field(default_factory=dict)  # 用户上传的初始输入
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    extra: dict[str, Any] = Field(default_factory=dict)
