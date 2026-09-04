"""LLM 调用抽象层 — 基于 IsoBase，支持工具/函数调用。

使用 IsoBase 的统一 LLM 客户端接口（BaseLLMClient），
支持 Anthropic Claude 和 OpenAI 兼容的 Provider，包括工具调用。

IsoBase 文档: https://github.com/landspark/IsoBase
"""

from __future__ import annotations

import asyncio
import json
import threading
from functools import partial
from typing import Any, Callable, Optional

try:
    from isobase.llm import AnthropicMessages, OpenAIChat
    from isobase.llm.entities import LLMResponse

    ISOBASE_AVAILABLE = True
except ImportError:  # 环境未安装 isobase 时降级：模块仍可导入，实际调用时报错
    ISOBASE_AVAILABLE = False
    AnthropicMessages = None  # type: ignore[assignment,misc]
    OpenAIChat = None  # type: ignore[assignment,misc]
    LLMResponse = Any  # type: ignore[assignment,misc]

from clipwright.config import settings
from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus

# 模块级客户端缓存：多个 LLMService 实例（各 Agent/Service 各自 new）共享同一
# IsoBase 客户端实例，避免每个实例重复构建连接/初始化（日志里反复出现
# "OpenAIChat initialized" 即此问题）。key = (provider, base_url, model, api_key)。
_client_cache: dict[tuple[str, Optional[str], Optional[str], Optional[str]], Any] = {}


# ── A6: 进程级 LLM 用量累计 ──
# 所有 LLMService 实例共享（含 Agent 内临时 new 的实例），供管线 per-agent
# 差值记账：_run_agent 前后各取一次快照，差值即该 Agent 的 token 消耗。
# 此前只有 StructureAgent 回写 _llm_usage，其余 Agent 的花费不入账，
# 导致 /api/stats 低报、月度预算熔断永不触发。
_usage_lock = threading.Lock()
_usage_totals = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def llm_usage_snapshot() -> dict[str, int]:
    """返回当前进程 LLM 用量累计快照（A6 per-agent 差值记账用）。"""
    with _usage_lock:
        return dict(_usage_totals)


def _record_global_usage(input_tokens: int, output_tokens: int) -> None:
    with _usage_lock:
        _usage_totals["input_tokens"] += max(0, int(input_tokens or 0))
        _usage_totals["output_tokens"] += max(0, int(output_tokens or 0))
        _usage_totals["calls"] += 1


def _client_cache_key(
    provider: str, base_url: Optional[str], model: Optional[str], api_key: Optional[str],
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    return (provider, base_url, model, api_key)


class LLMService:
    """基于 IsoBase 的 LLM 服务，支持文本生成和工具调用。"""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self._client: Optional[AnthropicMessages | OpenAIChat] = None
        self._flash_client: Optional[AnthropicMessages | OpenAIChat] = None
        self.last_usage: Optional[dict[str, int]] = None

    @property
    def client(self) -> AnthropicMessages | OpenAIChat:
        """懒初始化 IsoBase 客户端（主/专业模型）。"""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @property
    def flash_client(self) -> AnthropicMessages | OpenAIChat:
        """懒初始化 Flash 客户端（轻量模型，用于简单任务）。

        未配置 flash 参数时复用主 LLM 配置。
        """
        if self._flash_client is None:
            self._flash_client = self._build_client(flash=True)
        return self._flash_client

    def _build_client(self, flash: bool = False) -> AnthropicMessages | OpenAIChat:
        """根据配置构建对应的 IsoBase LLM 客户端。

        Args:
            flash: True 时使用 flash 模型配置（缺省项回退到主 LLM 配置）。
        """
        if not ISOBASE_AVAILABLE:
            raise RuntimeError(
                "isobase 未安装，无法构建 LLM 客户端。"
                "请运行: pip install 'isobase @ git+https://github.com/landspark/IsoBase.git'"
            )
        if flash:
            api_key = settings.llm_flash_api_key or settings.llm_api_key or None
            base_url = settings.llm_flash_base_url or settings.llm_base_url or None
            model = settings.llm_flash_model or settings.llm_model
            provider = settings.llm_flash_provider or self.provider
        else:
            api_key = settings.llm_api_key or None
            base_url = settings.llm_base_url or None
            model = settings.llm_model
            provider = self.provider
        instructions = settings.llm_instructions

        # 命中共享缓存：同一 (provider, base_url, model, api_key) 只构建一次客户端
        cache_key = _client_cache_key(provider, base_url, model, api_key)
        cached = _client_cache.get(cache_key)
        if cached is not None:
            return cached

        if provider == "anthropic":
            client: Any = AnthropicMessages(
                api_key=api_key,
                base_url=base_url,
                default_model=model,
                instructions=instructions,
                conversation_mode=False,
                max_tokens=8192,
            )
        elif provider in ("openai", "ollama"):
            client = OpenAIChat(
                api_key=api_key,
                base_url=base_url,
                default_model=model,
                instructions=instructions,
                conversation_mode=False,
                max_tokens=8192,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        _client_cache[cache_key] = client
        return client

    # ── 基础接口 ──

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        timeout: int = 120,
        use_flash: bool = False,
        max_retries: int = 2,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送非流式生成请求。

        Args:
            messages: 对话消息列表 [{"role": "user", "content": "..."}]
            model: 模型 ID，不传则用默认模型
            tools: Anthropic/OpenAI 格式的 tool schemas
            timeout: 超时秒数，默认 120s
            use_flash: True 时使用 flash 轻量模型（适合意图判断/分类等简单任务）
            max_retries: transient 错误（超时/429/5xx/连接错误）重试次数，默认 2
                （指数退避 1s/2s）；非 transient 失败（400/401/403/404/422）不重试。
            **kwargs: 透传给 IsoBase 的额外参数

        Returns:
            IsoBase 的 LLMResponse，包含 content / usage / tool_calls
        """
        if tools:
            kwargs["tools"] = tools
        # IsoBase 不支持 max_retries，仅传 timeout
        if timeout:
            kwargs["timeout"] = timeout
        # 过滤仅用于日志/追踪的元数据参数，不透传给 API
        kwargs.pop("pipeline_id", None)
        client = self.flash_client if use_flash else self.client
        logger.debug("LLM generate 请求: flash=%s, model=%s, timeout=%ds, messages=%s, tools=%s",
                     use_flash, model or settings.llm_model, timeout,
                     json.dumps(messages, ensure_ascii=False)[:500],
                     json.dumps(kwargs.get("tools", []), ensure_ascii=False)[:200])

        # E3: transient 错误指数退避重试（避免 transient 失败触发管线自愈全链路重做）
        def _is_transient(resp: Any) -> bool:
            sc = getattr(resp, "status_code", None)
            if sc is None:
                return not getattr(resp, "success", True)
            return int(sc) in (408, 409, 429) or int(sc) >= 500

        resp: Any = None
        for attempt in range(max_retries + 1):
            try:
                resp = await asyncio.to_thread(
                    partial(client.generate, messages=messages, model=model, **kwargs),
                )
                if resp.success or not _is_transient(resp):
                    break
                logger.warning(
                    "LLM generate transient 失败 (attempt %d/%d): status=%s, 退避重试",
                    attempt + 1, max_retries + 1, getattr(resp, "status_code", "?"),
                )
            except Exception as e:
                # 连接错误/超时等异常 → transient，继续重试
                if attempt >= max_retries:
                    logger.warning(
                        "LLM generate 异常 (attempt %d/%d): %s",
                        attempt + 1, max_retries + 1, e,
                    )
                    raise
                logger.warning(
                    "LLM generate 异常 (attempt %d/%d): %s, 退避重试",
                    attempt + 1, max_retries + 1, e,
                )
            if attempt < max_retries:
                await asyncio.sleep(2 ** (attempt + 1))  # 1s, 2s, 4s...

        logger.debug("LLM generate 响应: success=%s, content=%.500s, tool_calls=%s",
                     resp.success, resp.content or "",
                     [f"{tc.name}({tc.arguments[:100]})" for tc in (resp.tool_calls or [])])
        # 推送 LLM 响应 + 推理过程到 trace（用于 SSE 流式显示 LLM 思考）
        try:
            from clipwright.services.trace import add_tool_event as _push_llm
            reasoning = getattr(resp, 'reasoning_content', '') or ''
            display_text = (reasoning[:200] if reasoning else resp.content[:200]) if resp.content else ""
            if reasoning:
                _push_llm("🤔 LLM 推理", {"推理": reasoning[:500]})
            if resp.content:
                _push_llm("💬 LLM 响应", {"响应": resp.content[:500]})
        except Exception:
            pass

        # 缓存最近一次 usage，供 Agent 获取用量统计；并累计进程级用量（A6）
        if resp.success and resp.usage:
            self.last_usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            }
            _record_global_usage(self.last_usage["input_tokens"], self.last_usage["output_tokens"])

        return resp

    async def ask(
        self,
        prompt: str,
        tools: Optional[list[dict[str, Any]]] = None,
        use_flash: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """简化的单轮对话接口。"""
        logger.debug("LLM ask: flash=%s, prompt=%.300s, tools=%s", use_flash, prompt,
                     json.dumps(tools, ensure_ascii=False)[:200] if tools else "none")
        ask_kwargs = {**kwargs, "stream": False}
        if tools is not None:
            ask_kwargs["tools"] = tools
        client = self.flash_client if use_flash else self.client
        resp = await asyncio.to_thread(
            partial(client.ask, prompt=prompt, **ask_kwargs),
        )
        logger.debug("LLM ask 响应: success=%s, content=%.300s", resp.success, resp.content or "")
        # A6: ask 路径此前不记 usage——补齐 last_usage 与进程级累计
        if getattr(resp, "success", False) and getattr(resp, "usage", None):
            self.last_usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            }
            _record_global_usage(self.last_usage["input_tokens"], self.last_usage["output_tokens"])
        return resp

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        use_flash: bool = False,
        **kwargs: Any,
    ) -> str:
        """发送聊天请求，返回纯文本内容。"""
        logger.debug("LLM chat 请求: flash=%s, messages=%.500s, temperature=%s",
                     use_flash, json.dumps(messages, ensure_ascii=False)[:500], temperature)
        resp = await self.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            use_flash=use_flash,
            **kwargs,
        )
        if not resp.success:
            logger.error("LLM chat failed: status=%s, content=%.200s", resp.status_code, resp.content)
            raise RuntimeError(
                f"LLM request failed (status={resp.status_code}): {resp.content}"
            )
        logger.debug("LLM chat 响应: content=%.500s", resp.content)
        return resp.content

    async def structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Optional[dict[str, Any]] = None,
        use_flash: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """请求结构化输出（JSON）。"""
        logger.debug("LLM structured_output 请求: flash=%s, system=%.300s, user=%.300s, schema=%s",
                     use_flash, system_prompt[:300], user_prompt[:300],
                     json.dumps(output_schema, ensure_ascii=False)[:200] if output_schema else "none")
        # 选择与目标客户端一致的 provider 来构造消息格式
        provider = (settings.llm_flash_provider or self.provider) if use_flash else self.provider
        if provider == "anthropic":
            messages = [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        resp = await self.generate(messages=messages, use_flash=use_flash, **kwargs)
        if not resp.success:
            logger.error("LLM structured output failed: status=%s, content=%.200s", resp.status_code, resp.content)
            raise RuntimeError(
                f"LLM structured output failed (status={resp.status_code}): {resp.content}"
            )

        content = resp.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(line for line in lines if not line.startswith("```"))
        try:
            result = json.loads(content)
            logger.debug("LLM structured_output 响应: result=%.500s",
                         json.dumps(result, ensure_ascii=False)[:500])
            return result
        except json.JSONDecodeError:
            logger.warning("LLM structured output JSON parse failed, returning raw content")
            logger.debug("LLM structured_output 原始内容: %s", content[:500])
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

        logger.debug("LLM with_tools 启动: system=%.300s, user=%.300s, tools=%s, max_rounds=%s",
                     system_prompt[:300], user_prompt[:300],
                     json.dumps(tools, ensure_ascii=False)[:300], max_tool_rounds)
        tool_executor_is_coro = asyncio.iscoroutinefunction(tool_executor)
        is_first_round = True

        for _round in range(max_tool_rounds):
            kw = {**base_kwargs, "tools": tools} if is_first_round else base_kwargs
            is_first_round = False

            resp = await self.generate(messages=messages, **kw)
            logger.debug("LLM with_tools 第%d轮: success=%s, tool_calls=%d, content=%.200s",
                         _round + 1, resp.success, len(resp.tool_calls or []), resp.content or "")

            if not resp.success:
                logger.error("LLM with_tools request failed: status=%s", resp.status_code)
                return resp

            if not resp.tool_calls:
                logger.debug("LLM with_tools 第%d轮完成: 无工具调用", _round + 1)
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
                    logger.debug("LLM with_tools 工具调用 %s → input=%s", tc.name,
                                 json.dumps(tool_input, ensure_ascii=False)[:200])
                    result = await self._do_tool_call(tool_executor, tool_executor_is_coro, tc.name, tool_input)
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    logger.debug("LLM with_tools 工具结果 %s → %s", tc.name, result_str[:300])
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
                    logger.debug("LLM with_tools OpenAI 工具 %s → input=%s", tc.name,
                                 json.dumps(tool_input, ensure_ascii=False)[:200])
                    result = await self._do_tool_call(tool_executor, tool_executor_is_coro, tc.name, tool_input)
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    logger.debug("LLM with_tools OpenAI 工具结果 %s → %s", tc.name, result_str[:300])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

        logger.debug("LLM with_tools 结束: tool_calls 耗尽或达上限, 最终消息数=%d", len(messages))
        logger.info("LLM with_tools 最终响应: content=%.500s", resp.content or "(空)")
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
            logger.error("LLM _do_tool_call failed: name=%s, error=%s", name, e)
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
        if not ISOBASE_AVAILABLE:
            raise RuntimeError(
                "isobase 未安装，无法构建 LLM 客户端。"
                "请运行: pip install 'isobase @ git+https://github.com/landspark/IsoBase.git'"
            )
        p = provider or settings.llm_provider
        k = api_key or settings.llm_api_key or None
        m = model or settings.llm_model
        u = base_url or settings.llm_base_url or None
        instructions = settings.llm_instructions

        # 同样走共享缓存：视觉等独立配置的客户端也复用（同一 key 只建一次）
        cache_key = _client_cache_key(p, u, m, k)
        cached = _client_cache.get(cache_key)
        if cached is not None:
            return cached

        if p == "anthropic":
            client: Any = AnthropicMessages(
                api_key=k, base_url=u, default_model=m,
                instructions=instructions, conversation_mode=False, max_tokens=8192,
            )
        elif p in ("openai", "ollama"):
            client = OpenAIChat(
                api_key=k, base_url=u, default_model=m,
                instructions=instructions, conversation_mode=False, max_tokens=8192,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {p}")
        _client_cache[cache_key] = client
        return client

    def reset(self) -> None:
        """重置客户端（重新初始化时使用）。"""
        self._client = None
        self._flash_client = None
        # 清理本实例对应的共享缓存项，保证配置热更新后下次构建新客户端
        try:
            for provider, base_url, model, api_key in (
                (self.provider, settings.llm_base_url or None, settings.llm_model, settings.llm_api_key or None),
                (settings.llm_flash_provider or self.provider,
                 settings.llm_flash_base_url or settings.llm_base_url or None,
                 settings.llm_flash_model or settings.llm_model,
                 settings.llm_flash_api_key or settings.llm_api_key or None),
            ):
                _client_cache.pop(_client_cache_key(provider, base_url, model, api_key), None)
        except Exception:
            pass
