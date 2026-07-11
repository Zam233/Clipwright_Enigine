"""Skill 数据模型 — 技能定义和执行结果的标准契约。

Skill 是比 Tool 更高层级的可组合能力：
- Tool: 原子操作（如 scene_detect、audio_extract）
- Skill: 编排多个工具完成一个业务目标（如 analyze_video_structure）
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SkillStatus(str, Enum):
    """技能执行状态。"""
    SUCCESS = "success"
    ERROR = "error"
    NOT_FOUND = "not_found"
    DEPENDENCY_MISSING = "dependency_missing"
    TOOL_FAILED = "tool_failed"


class SkillExecResult(BaseModel):
    """技能执行结果的标准化格式。"""
    status: SkillStatus = SkillStatus.SUCCESS
    skill_name: str = Field(default="", description="技能名称")
    output: dict[str, Any] = Field(
        default_factory=dict, description="技能输出数据"
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="技能内部调用的工具记录 [{tool, input, output}]",
    )
    error: Optional[str] = Field(default=None, description="错误信息")
    warning: Optional[str] = Field(default=None, description="警告信息")

    model_config = {"use_enum_values": True}


class SkillInfo(BaseModel):
    """技能的元信息。"""
    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="参数名 → {type, description, required, default}",
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="技能依赖的工具列表",
    )
    available: bool = Field(
        default=False, description="当前环境是否可用（依赖的工具是否都可用）"
    )
