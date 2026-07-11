"""工具层数据模型 — ToolRegistry 和执行结果的标准契约。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

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
