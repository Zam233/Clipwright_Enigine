"""LLM 调用抽象层 — 基于 IsoBase，支持工具/函数调用。

使用 IsoBase 的统一 LLM 客户端接口（BaseLLMClient），
支持 Anthropic Claude 和 OpenAI 兼容的 Provider，包括工具调用。

IsoBase 文档: https://github.com/landspark/IsoBase
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any, Callable, Optional

from isobase.llm import AnthropicMessages, OpenAIChat
from isobase.llm.entities import LLMResponse

from clipwright.config import settings
from clipwright.schema.tool import ToolExecResult, ToolStatus


class LLMService:
    """基于 IsoBase 的 LLM 服务，支持文本生成和工具调用。"""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self._client: Optional[AnthropicMessages | OpenAIChat] = None

    @property
    def client(self) -> AnthropicMessages | OpenAIChat:
        """懒初始化 IsoBase 客户端。"""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> AnthropicMessages | OpenAIChat:
        """根据配置构建对应的 IsoBase LLM 客户端。"""
        api_key = settings.llm_api_key or None
        base_url = settings.llm_base_url or None
        model = settings.llm_model
        instructions = settings.llm_instructions

        if self.provider == "anthropic":
            return AnthropicMessages(
                api_key=api_key,
                base_url=base_url,
                default_model=model,
                instructions=instructions,
                conversation_mode=False,
                max_tokens=8192,
            )
        elif self.provider in ("openai", "ollama"):
            return OpenAIChat(
                api_key=api_key,
                base_url=base_url,
                default_model=model,
                instructions=instructions,
                conversation_mode=False,
                max_tokens=8192,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    # ── 基础接口 ──

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送非流式生成请求。

        Args:
            messages: 对话消息列表 [{"role": "user", "content": "..."}]
            model: 模型 ID，不传则用默认模型
            tools: Anthropic/OpenAI 格式的 tool schemas
            **kwargs: 透传给 IsoBase 的额外参数

        Returns:
            IsoBase 的 LLMResponse，包含 content / usage / tool_calls
        """
        if tools:
            kwargs["tools"] = tools
        return await asyncio.to_thread(
            partial(self.client.generate, messages=messages, model=model, **kwargs),
        )

    async def ask(
        self,
        prompt: str,
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """简化的单轮对话接口。

        Args:
            prompt: 用户输入文本
            tools: Anthropic/OpenAI 格式的 tool schemas
            **kwargs: 透传给 IsoBase 的额外参数

        Returns:
            IsoBase 的 LLMResponse
        """
        return await asyncio.to_thread(
            partial(self.client.ask, prompt=prompt, stream=False, tools=tools, **kwargs),
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """发送聊天请求，返回纯文本内容。

        高层封装：调用 generate() 后提取 content 字段。
        """
        resp = await self.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if not resp.success:
            raise RuntimeError(
                f"LLM request failed (status={resp.status_code}): {resp.content}"
            )
        return resp.content

    async def structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """请求结构化输出（JSON）。"""
        if self.provider == "anthropic":
            messages = [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        resp = await self.generate(messages=messages, **kwargs)
        if not resp.success:
            raise RuntimeError(
                f"LLM structured output failed (status={resp.status_code}): {resp.content}"
            )

        content = resp.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(line for line in lines if not line.startswith("```"))
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"content": content}

    # ── 工具调用支持 ──

    async def with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_executor: Callable[[str, dict[str, Any]], ToolExecResult | dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tool_rounds: int = 10,
        **kwargs: Any,
    ) -> LLMResponse:
        """执行带工具调用循环的 LLM 请求。

        流程：
        1. 发送初始系统 + 用户 prompt 和工具定义给 LLM
        2. LLM 返回文本和/或 tool_calls
        3. 若有 tool_calls → 执行工具 → 结果送回 LLM
        4. 重复直到 LLM 不再调用工具或达到 max_tool_rounds
        5. 返回最终 LLMResponse

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            tool_executor: 执行工具的回调函数 fn(tool_name, tool_input) → dict
            tools: Anthropic/OpenAI 格式的工具定义列表
            max_tool_rounds: 最大工具调用轮数（防止无限循环）
            **kwargs: 透传给 LLMService.generate() 的额外参数

        Returns:
            最终的 LLMResponse（不再包含 tool_calls 的轮次）
        """
        if self.provider == "anthropic":
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": user_prompt},
            ]
            base_kwargs = {**kwargs, "system": system_prompt}
            first_round_kwargs = {**base_kwargs, "tools": tools}
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            base_kwargs = {**kwargs}
            first_round_kwargs = {**base_kwargs, "tools": tools}

        tool_executor_is_coro = asyncio.iscoroutinefunction(tool_executor)
        is_first_round = True

        for _round in range(max_tool_rounds):
            kw = {**base_kwargs, "tools": tools} if is_first_round else base_kwargs
            is_first_round = False

            resp = await self.generate(messages=messages, **kw)

            if not resp.success:
                return resp

            if not resp.tool_calls:
                return resp

            if self.provider == "anthropic":
                # Anthropic: tool_use 块嵌入在 content 数组
                content_blocks: list[dict[str, Any]] = []
                if resp.content:
                    content_blocks.append({"type": "text", "text": resp.content})

                for tc in resp.tool_calls:
                    parsed = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": parsed,
                    })

                messages.append({"role": "assistant", "content": content_blocks})

                for tc in resp.tool_calls:
                    tool_input = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    result = await self._do_tool_call(tool_executor, tool_executor_is_coro, tc.name, tool_input)
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tc.id, "content": result_str}],
                    })
            else:
                # OpenAI: tool_calls 在 assistant message 的 tool_calls 字段
                openai_tool_calls = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in resp.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": resp.content or None,
                    "tool_calls": openai_tool_calls,
                })

                for tc in resp.tool_calls:
                    tool_input = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    result = await self._do_tool_call(tool_executor, tool_executor_is_coro, tc.name, tool_input)
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

        return resp

    @staticmethod
    async def _do_tool_call(
        executor: Any, is_coro: bool, name: str, inp: dict[str, Any]
    ) -> Any:
        """执行一次工具调用，同步/异步兼容。"""
        try:
            if is_coro:
                return await executor(name, inp)
            return executor(name, inp)
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def build_client(
        provider: str = "",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ) -> AnthropicMessages | OpenAIChat:
        """使用指定参数构建 LLM 客户端（不依赖实例配置）。

        用于需要独立 LLM 配置的场景（如视觉识别使用不同的模型/API）。
        """
        p = provider or settings.llm_provider
        k = api_key or settings.llm_api_key or None
        m = model or settings.llm_model
        u = base_url or settings.llm_base_url or None
        instructions = settings.llm_instructions

        if p == "anthropic":
            return AnthropicMessages(
                api_key=k, base_url=u, default_model=m,
                instructions=instructions, conversation_mode=False, max_tokens=8192,
            )
        elif p in ("openai", "ollama"):
            return OpenAIChat(
                api_key=k, base_url=u, default_model=m,
                instructions=instructions, conversation_mode=False, max_tokens=8192,
            )
        raise ValueError(f"Unsupported LLM provider: {p}")

    def reset(self) -> None:
        """重置客户端（重新初始化时使用）。"""
        self._client = None
