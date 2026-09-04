"""Agent 基类。

每个 Agent 是一个 LangGraph 节点，包含策略注册表。
Agent 本身不写死逻辑，它只是"调度器"——根据 Persona 和视频类型选择策略。
"""

from __future__ import annotations

import asyncio
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


async def unified_llm_call(
    name: str,
    coro_factory,
    retries: int = 1,
    timeout: float = 300,
    retry_delay_sec: float = 1.5,
):
    """生产加固 1.4：全仓统一的 LLM/工具调用收口 — 超时 + 瞬时失败重试（线性退避）。

    重试仅针对「请求确已失败」的瞬时错误（rate limit/429/connection/reset），
    永久错误直接抛出，由调用方决定降级。
    F7: 超时不再重试——底层请求经 asyncio.to_thread 执行，wait_for 超时无法
    真正取消线程内的 provider 请求；此时重试会造成同一请求双份并发（双份
    token 计费 + 线程泄漏）。超时直接抛出，由调用方走兜底降级。
    """
    from clipwright.config import logger

    last_exc: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        except asyncio.TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001 — 按错误特征判定是否瞬时
            low = str(e).lower()
            transient = any(k in low for k in (
                "timeout", "rate limit", "429", "connection", "reset", "temporarily",
            ))
            if not transient:
                raise
            last_exc = e
        if attempt < retries:
            logger.warning(
                "%s: 调用失败（%s），%.1fs 后第 %d 次重试",
                name, last_exc, retry_delay_sec * (attempt + 1), attempt + 1,
            )
            await asyncio.sleep(retry_delay_sec * (attempt + 1))
    raise last_exc  # type: ignore[misc]


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
        return await asyncio.wait_for(coro_factory(), timeout=timeout or self.timeout_sec)

    async def llm_call_with_retry(
        self,
        coro_factory,
        retries: int = 1,
        timeout: float | None = None,
        retry_delay_sec: float = 1.5,
    ):
        """生产加固 1.4：统一 LLM 调用收口（委托模块级 unified_llm_call）。"""
        return await unified_llm_call(
            self.agent_name, coro_factory,
            retries=retries, timeout=timeout or self.timeout_sec,
            retry_delay_sec=retry_delay_sec,
        )

    async def llm_or_fallback(self, coro_factory, fallback, timeout: float | None = None, retries: int = 1):
        """LLM 调用 + 重试 + 超时兜底：失败一律返回 fallback 并记录日志。"""
        from clipwright.config import logger

        try:
            return await self.llm_call_with_retry(coro_factory, retries=retries, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("%s: LLM 调用超时，使用兜底", self.agent_name)
            return fallback
        except Exception as e:
            logger.warning("%s: LLM 调用失败（%s），使用兜底", self.agent_name, e)
            return fallback

    @staticmethod
    def quality_issues_hint(context: Any) -> str:
        """C6: 自愈重做时提取上一轮质检问题段落（注入 LLM 提示词尾部）。

        pipeline_v2 自愈循环此前把 _quality_issues 写入 extra_params，但没有任何
        Agent 读取——被要求重做的 Agent 不知道为什么重做。无问题/非自愈时返回空串。
        """
        issues = (getattr(context, "extra_params", {}) or {}).get("_quality_issues") or []
        lines: list[str] = []
        for item in issues[:5]:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('severity', '')}] {item.get('category', '')}: "
                    f"{str(item.get('message', ''))[:120]}"
                )
        if not lines:
            return ""
        return ("\n\n## 上一轮质检发现问题（本次重做必须针对性修复，不要重复同类问题）\n"
                + "\n".join(lines))
