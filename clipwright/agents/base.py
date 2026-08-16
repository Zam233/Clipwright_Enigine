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

    # ── 统一基座（P5）：超时 + 优雅降级，供各 Agent 复用 ──

    async def run_with_timeout(self, coro_factory, timeout: float | None = None):
        """带统一超时的 LLM/工具调用封装（超时抛 asyncio.TimeoutError，由调用方降级）。

        用法::

            try:
                result = await agent.run_with_timeout(lambda: llm.ask(...))
            except asyncio.TimeoutError:
                result = fallback

        超时默认取 ``self.timeout_sec``（各 Agent 可覆盖）。
        """
        import asyncio

        return await asyncio.wait_for(coro_factory(), timeout=timeout or self.timeout_sec)

    async def llm_or_fallback(self, coro_factory, fallback, timeout: float | None = None):
        """LLM 调用 + 超时兜底：超时/异常一律返回 fallback 并记录日志。"""
        from clipwright.config import logger

        try:
            return await self.run_with_timeout(coro_factory, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("%s: LLM 调用超时，使用兜底", self.agent_name)
            return fallback
        except Exception as e:
            logger.warning("%s: LLM 调用失败（%s），使用兜底", self.agent_name, e)
            return fallback
