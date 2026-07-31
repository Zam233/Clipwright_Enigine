"""Agent 基类。

每个 Agent 是一个 LangGraph 节点，包含策略注册表。
Agent 本身不写死逻辑，它只是"调度器"——根据 Persona 和视频类型选择策略。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from clipwright.schema.agent import AgentContext, AgentDecision

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


def uid(prefix: str = "ag") -> str:
    """生成短唯一 ID，供 Agent 共用。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class BaseAgent(ABC, Generic[I, O]):
    """Agent 基类。"""

    agent_name: str = ""
    timeout_sec: int = 300  # LLM 调用默认超时（秒）——大 prompt + 多工具调用时 120s 不足

    def __init__(self) -> None:
        self._strategy_registry: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, input_data: I, context: AgentContext) -> O:
        """执行 Agent 的核心逻辑。"""
        ...

    def register_strategy(self, name: str, strategy: Any) -> None:
        """注册策略到策略注册表。"""
        self._strategy_registry[name] = strategy

    def get_strategy(self, name: str) -> Any:
        """从注册表获取策略。"""
        return self._strategy_registry.get(name)

    def build_error_output(self, error_msg: str, output_cls: type[O]) -> O:
        """构造错误输出。"""
        return output_cls(
            decision=AgentDecision.FAIL,
            error=error_msg,
        )
