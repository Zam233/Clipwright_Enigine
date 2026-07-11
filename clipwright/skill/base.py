"""Skill 基类和接口定义。

Skill = 可组合的高级能力。
与 Tool 的区别：
- Tool: 原子操作，调用 FFmpeg/OpenCV 等外部命令
- Skill: 编排多个 Tool + 自有逻辑，完成一个业务目标
"""

from __future__ import annotations

import inspect
import typing
from abc import ABC, abstractmethod
from typing import Any

from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.tool.registry import ToolRegistry


class BaseSkill(ABC):
    """技能基类。

    一个 Skill 可以编排多个 Tool 来完成一个业务目标。
    """

    name: str = ""
    description: str = ""
    # 本技能依赖的工具列表（用于可用性检查）
    required_tools: list[str] = []

    @abstractmethod
    async def execute(self, **kwargs: Any) -> SkillExecResult:
        """执行技能。"""
        ...

    def is_available(self) -> bool:
        """检查技能是否可用（所有依赖的工具都已注册且可用）。"""
        if not self.required_tools:
            return True
        for tool_name in self.required_tools:
            tool = ToolRegistry.get(tool_name)
            if tool is None or not tool.is_available():
                return False
        return True

    async def _run_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """技能内部调用工具的统一入口。"""
        result = await ToolRegistry.execute(name, **kwargs)
        return result.model_dump(mode="json")

    def to_llm_tool(self, fmt: str = "anthropic") -> dict[str, Any]:
        """生成 LLM tool schema（同 BaseTool 接口，方便 Agent 统一调用）。"""
        params = self._infer_parameters()
        input_schema: dict[str, Any] = {"type": "object", "properties": {}}
        required: list[str] = []
        for pname, pinfo in params.items():
            if pname in ("kwargs", "self"):
                continue
            ptype = pinfo.get("type", "string")
            input_schema["properties"][pname] = {
                "type": ptype,
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required", False):
                required.append(pname)
        if required:
            input_schema["required"] = required

        if fmt == "openai":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": input_schema,
                },
            }
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }

    def _infer_parameters(self) -> dict[str, dict[str, Any]]:
        """从 execute() 方法签名推断参数信息（同 BaseTool 逻辑）。"""
        sig = inspect.signature(self.execute)
        try:
            hints = typing.get_type_hints(self.execute)
        except Exception:
            hints = {}
        params: dict[str, dict[str, Any]] = {}
        for pname, param in sig.parameters.items():
            if pname in ("self", "kwargs", "args"):
                continue
            ann = hints.get(pname, param.annotation)
            pinfo: dict[str, Any] = {
                "type": _type_name(ann),
                "required": param.default is inspect.Parameter.empty,
                "description": "",
            }
            if param.default is not inspect.Parameter.empty and param.default is not None:
                pinfo["default"] = param.default
            params[pname] = pinfo
        return params


def _type_name(annotation: Any) -> str:
    """类型注解 → JSON Schema 类型名（精简版，同 tool/base.py 逻辑）。"""
    if annotation is inspect.Parameter.empty or annotation is None:
        return "string"
    origin = typing.get_origin(annotation)
    if origin is not None:
        if origin is typing.Union:
            non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
            return _type_name(non_none[0]) if non_none else "string"
        if origin in (list, tuple, set, typing.List, typing.Tuple, typing.Set):
            return "array"
        if origin in (dict, typing.Dict):
            return "object"
    if isinstance(annotation, type):
        if issubclass(annotation, str):
            return "string"
        if issubclass(annotation, (int, float)):
            return "number"
        if issubclass(annotation, bool):
            return "boolean"
        if issubclass(annotation, (list, tuple, set)):
            return "array"
        if issubclass(annotation, dict):
            return "object"
    return "string"
