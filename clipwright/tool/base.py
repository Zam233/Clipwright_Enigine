"""工具基类和接口定义。

所有 BaseTool 子类须实现 execute() 并设置 name/description。
ToolRegistry 通过 name 进行注册和分发。

LLM 工具调用支持：
- to_llm_tool(): 将工具转换为 Anthropic/OpenAI tool schema
- AgentToolkit: 编译一组工具供 Agent 使用
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.utils.type_utils import infer_parameters_from_signature


def _json_type(py_type: str) -> str:
    """Python 类型名 → JSON Schema 类型名。"""
    mapping = {
        "str": "string", "string": "string",
        "int": "number", "float": "number", "number": "number",
        "bool": "boolean", "boolean": "boolean",
        "list": "array", "tuple": "array", "array": "array",
        "dict": "object", "object": "object",
    }
    return mapping.get(py_type.lower(), "string") if isinstance(py_type, str) else "string"


class BaseTool(ABC):
    """原子能力工具基类。"""

    name: str = ""
    description: str = ""
    # 工具依赖的外部命令（如 ffmpeg），用于可用性检测
    dependencies: list[str] = []

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具操作。"""
        ...

    def is_available(self) -> bool:
        """检查工具在当前环境是否可用（依赖是否满足）。"""
        if not self.dependencies:
            return True
        for cmd in self.dependencies:
            if shutil.which(cmd) is None:
                return False
        return True

    def to_llm_tool(self, fmt: str = "anthropic") -> dict[str, Any]:
        """将工具定义转换为 LLM API 可识别的 tool schema。

        Args:
            fmt: "anthropic" | "openai"

        Returns:
            Anthropic Messages API tool 格式或 OpenAI tool 格式的 dict。
        """
        params = self._infer_parameters()
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {},
        }
        required: list[str] = []
        for pname, pinfo in params.items():
            if pname in ("kwargs", "self"):
                continue
            ptype = pinfo.get("type", "string")
            input_schema["properties"][pname] = {
                "type": _json_type(ptype),
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

        # Anthropic 默认格式
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }

    def _infer_parameters(self) -> dict[str, dict[str, Any]]:
        """从 execute() 方法签名推断参数信息（带缓存）。"""
        if not hasattr(self, "_param_cache"):
            self._param_cache: dict[str, dict[str, Any]] | None = None
        if self._param_cache is None:
            self._param_cache = infer_parameters_from_signature(self.execute)
        return self._param_cache


class AgentToolkit:
    """Agent 工具包 — 编译一组工具供 LLM Agent 使用。

    封装 ToolRegistry 的查询 + 执行，暴露给 LLM Agent 的 execute() 方法。
    """

    def __init__(
        self,
        tool_names: list[str],
        fmt: str = "anthropic",
    ) -> None:
        """初始化工具包。

        Args:
            tool_names: 要包含的工具名列表（来自 ToolRegistry）
            fmt: llm tool schema 格式
        """
        from clipwright.tool.registry import ToolRegistry

        self._tools: dict[str, BaseTool] = {}
        for name in tool_names:
            tool = ToolRegistry.get(name)
            if tool is not None and tool.is_available():
                self._tools[name] = tool
        self._fmt = fmt

    @property
    def llm_tools(self) -> list[dict[str, Any]]:
        """获取 LLM API 可用的 tool schemas。"""
        return [t.to_llm_tool(self._fmt) for t in self._tools.values()]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    @property
    def available(self) -> bool:
        return len(self._tools) > 0

    async def execute_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> ToolExecResult:
        """执行来自 LLM tool_call 的工具请求。"""
        from clipwright.tool.registry import ToolRegistry

        return await ToolRegistry.execute(tool_name, **tool_input)
