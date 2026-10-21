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
from typing import Any, AsyncIterator, Callable, Optional

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


def _resolve_role_binding(role: str) -> dict[str, Any] | None:
    """B3：按角色解析 Provider 源凭据（无绑定/存储不可用 → None，调用方回退）。

    角色 → 源 → 模型/凭据的映射由 `provider_sources` 统一维护；未配置绑定或
    存储层异常时返回 None，调用方继续使用 settings 中的旧槽位配置（向后兼容）。
    """
    if not role:
        return None
    try:
        from clipwright.services import provider_sources as _ps
        return _ps.resolve(role)
    except Exception:  # noqa: BLE001 — 解析失败不阻断（回退旧配置）
        return None


def _resolve_llm_provider(provider: str) -> Any | None:
    """解析 LLM provider 实现（懒注册内置 + 门面查询；异常 → None 走 legacy 兜底）。

    B-1：provider 协议的唯一查询点——客户端构建、消息规范、能力校验共用。
    """
    try:
        from clipwright.providers.llm import ensure_llm_providers_registered
        ensure_llm_providers_registered()
        from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
        impl = ContributionRegistry.resolve(ContributionKind.LLM_PROVIDER, provider)
        return impl.object if impl is not None else None
    except Exception:  # noqa: BLE001 — 解析失败不阻断（调用方走 legacy 分支）
        return None


def clear_client_cache() -> None:
    """清空共享客户端缓存（配置热更新后按新配置重建连接）。"""
    _client_cache.clear()


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
    transport: str = "",
) -> tuple[str, Optional[str], Optional[str], Optional[str], str]:
    # transport 是超时/代理/自定义头的指纹（B4）——同一凭据不同传输参数
    # 必须各自缓存，否则代理/超时会串味。
    return (provider, base_url, model, api_key, transport)


def _transport_fingerprint(
    timeout_sec: float = 0.0, proxy: str = "", headers: Optional[dict[str, Any]] = None,
) -> str:
    if not timeout_sec and not proxy and not headers:
        return ""
    hdr = "|".join(f"{k}={v}" for k, v in sorted((headers or {}).items()))
    return f"t={timeout_sec};p={proxy};h={hdr}"


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

    def _routed_client_and_model(self, use_flash: bool = False):
        """B4：若当前上下文有模型路由，返回 (client, model) 覆盖默认模型。

        - 无路由（`model_routes` 留空 / off 模式）→ 返回默认 client 与 None，
          行为与改造前完全一致；
        - 同 provider → 复用现有 client，只换 model；
        - 跨 provider → 用该 provider 的凭据构建（共享客户端缓存），凭据缺失时
          记 warning 并回退默认，绝不静默串用别的 provider 的 key。
        """
        try:
            from clipwright.services.model_router import active_route, resolve_provider_credentials

            route = active_route()
        except Exception:  # noqa: BLE001
            route = None
        if route is None:
            return (self.flash_client if use_flash else self.client), None

        default_provider = (
            settings.llm_flash_provider or self.provider
        ) if use_flash else self.provider
        if not route.provider or route.provider == default_provider:
            return (self.flash_client if use_flash else self.client), (route.model or None)

        creds = resolve_provider_credentials(route.provider)
        if not creds:
            logger.warning(
                "模型路由目标 provider 无可用凭据，沿用默认模型: %s", route.key,
            )
            return (self.flash_client if use_flash else self.client), (route.model or None)

        try:
            client = self._construct_client(
                route.provider,
                api_key=creds.get("api_key"),
                base_url=creds.get("base_url"),
                model=route.model or creds.get("model") or settings.llm_model,
                instructions=settings.llm_instructions,
                max_tokens=8192,
                flash=False,
                timeout_sec=float(creds.get("timeout_sec") or 0.0),
                proxy=str(creds.get("proxy") or ""),
                headers=dict(creds.get("headers") or {}) or None,
            )
        except Exception as e:  # noqa: BLE001 — 跨 provider 构造失败回退默认
            logger.warning("跨 provider 客户端构建失败（回退默认）: %s", e)
            return (self.flash_client if use_flash else self.client), (route.model or None)
        return client, (route.model or None)

    def _build_client(self, flash: bool = False) -> AnthropicMessages | OpenAIChat:
        """根据配置构建对应的 IsoBase LLM 客户端（委托唯一构造点）。

        Args:
            flash: True 时使用 flash 模型配置（缺省项回退到主 LLM 配置）。
        """
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
        bound = _resolve_role_binding("flash" if flash else "main")
        timeout_sec, proxy, headers = 0.0, "", None
        if bound:
            provider = bound.get("provider") or provider
            api_key = bound.get("api_key") or api_key
            base_url = bound.get("base_url") or base_url
            model = bound.get("model") or model
            timeout_sec = float(bound.get("timeout_sec") or 0.0)
            proxy = str(bound.get("proxy") or "")
            headers = dict(bound.get("headers") or {}) or None
        return self._construct_client(
            provider, api_key=api_key, base_url=base_url, model=model,
            instructions=settings.llm_instructions, max_tokens=8192, flash=flash,
            timeout_sec=timeout_sec, proxy=proxy, headers=headers,
        )

    @staticmethod
    def _construct_client(
        provider: str,
        *,
        api_key: str | None,
        base_url: str | None,
        model: str,
        instructions: str,
        max_tokens: int = 8192,
        flash: bool = False,
        timeout_sec: float = 0.0,
        proxy: str = "",
        headers: Optional[dict[str, Any]] = None,
    ) -> Any:
        """唯一构造点（B-1a/B-1）——provider 协议只在这里查询一次。

        旧的实例级 `_build_client` 与静态 `build_client` 均委托到此，
        消除两份重复分支（方案 §13.3 修正 3）。B4：超时/代理/自定义头
        由角色绑定解析后透传到 provider 的传输层。
        """
        if not ISOBASE_AVAILABLE:
            raise RuntimeError(
                "isobase 未安装，无法构建 LLM 客户端。"
                "请运行: pip install 'isobase @ git+https://github.com/landspark/IsoBase.git'"
            )
        # 命中共享缓存：同 (provider, base_url, model, api_key, transport) 只构建一次
        cache_key = _client_cache_key(
            provider, base_url, model, api_key,
            _transport_fingerprint(timeout_sec, proxy, headers),
        )
        cached = _client_cache.get(cache_key)
        if cached is not None:
            return cached

        impl = _resolve_llm_provider(provider)
        if impl is None:
            from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
            registered = [c.id for _, c in ContributionRegistry.list(ContributionKind.LLM_PROVIDER)]
            raise ValueError(f"未安装 LLM provider: {provider}；已注册: {registered}")

        # B2：协议方法缺失给可读错误（注册时已校验，此处兜住手工注册/dispatch=False 的路径）
        build = getattr(impl, "build_client", None)
        if not callable(build):
            raise ValueError(
                f"LLM provider {provider} 未实现 build_client()，不符合 LLMProvider 协议"
            )
        # P1：按 provider 实际签名过滤传输参数——存量第三方 provider 是按 B4 之前的
        # 签名写的（没有 timeout_sec/proxy/headers），无条件传会让它们每次调用都
        # TypeError。不支持的参数在这里丢掉；若用户确实配了非默认值则给一次告警。
        from clipwright.providers.base import supported_transport_params, _warn_once
        accepted = supported_transport_params(impl)
        extra: dict[str, Any] = {}
        if "timeout_sec" in accepted:
            extra["timeout_sec"] = timeout_sec
        if "proxy" in accepted:
            extra["proxy"] = proxy
        if "headers" in accepted:
            extra["headers"] = headers
        if accepted != {"timeout_sec", "proxy", "headers"}:
            configured = (timeout_sec > 0) or bool(proxy) or bool(headers)
            if configured:
                _warn_once(
                    impl,
                    "provider 不支持传输参数（timeout/proxy/headers），"
                    "源里配置的这些项将被忽略；升级 provider 后生效",
                )
        client: Any = build(
            api_key=api_key, base_url=base_url, model=model,
            instructions=instructions, max_tokens=max_tokens, flash=flash,
            **extra,
        )
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
        # B4：路由覆盖（无路由时返回默认 client 与 None）
        client, _routed_model = self._routed_client_and_model(use_flash)
        if _routed_model and not model:
            model = _routed_model
        logger.debug("LLM generate 请求: flash=%s, model=%s, timeout=%ds, messages=%s, tools=%s",
                     use_flash, model or settings.llm_model, timeout,
                     json.dumps(messages, ensure_ascii=False)[:500],
                     json.dumps(kwargs.get("tools", []), ensure_ascii=False)[:200])

        # transient 错误指数退避重试（避免 transient 失败触发管线自愈全链路重做）
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
        client, _routed_model = self._routed_client_and_model(use_flash)
        if _routed_model and not ask_kwargs.get("model"):
            ask_kwargs["model"] = _routed_model
        resp = await asyncio.to_thread(
            partial(client.ask, prompt=prompt, **ask_kwargs),
        )
        logger.debug("LLM ask 响应: success=%s, content=%.300s", resp.success, resp.content or "")
        # ask 路径此前不记 usage——补齐 last_usage 与进程级累计
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
        # 选择与目标客户端一致的 provider 来构造消息格式（B4：路由优先）
        _route_provider = ""
        try:
            from clipwright.services.model_router import active_route

            _route = active_route()
            _route_provider = str(getattr(_route, "provider", "") or "") if _route else ""
        except Exception:  # noqa: BLE001
            _route_provider = ""
        provider = _route_provider or (
            (settings.llm_flash_provider or self.provider) if use_flash else self.provider
        )
        # B-1：消息格式规范由 provider 协议提供（legacy 分支兜底）
        # B2/B3：缺方法或能力未声明时由访问器给最小可用语义 + 可读告警
        impl = _resolve_llm_provider(provider)
        if impl is not None:
            from clipwright.providers.base import llm_normalize_messages
            messages = llm_normalize_messages(impl, user_prompt, system_prompt)
        elif provider == "anthropic":
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
        if not content:
            # `success=True` + **空 content** 是个很容易被误读的状态（实测：
            # 推理型模型在 max_tokens 偏小时会把预算全用在推理上，返回 200 但正文为空，
            # 于是调用方拿到 `{}`/`{"content": ""}`，看起来像"模型没意见"而不是"额度不够"）。
            # 这里必须吵出来，并给出最可能的原因。
            logger.warning(
                "LLM 返回空内容（status=%s, max_tokens=%s）——若为推理型模型，"
                "通常是 max_tokens 偏小、预算被推理过程用完；请调大 max_tokens 重试",
                resp.status_code, kwargs.get("max_tokens"))
            return {}
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

    # 轮68 修正：isobase 已提供 generate_stream()（Anthropic/OpenAI 双实现，
    # 同步迭代器 yield 增量 LLMResponse）。经线程→Queue 桥接为异步生成器后
    # 即可实现 requirements chat 的 token 级真流式（见 stream_generate）。

    async def stream_generate(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        timeout: int = 120,
        use_flash: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[LLMResponse]:
        """流式生成——同步迭代器经线程→Queue 桥接为异步增量 yield。

        每个 yield 是上游 LLMResponse 增量块（content 为本次新增文本片段）；
        最后一个块聚合完整 usage 与 tool_calls（content 为空）。异常时抛出。
        """
        client = self.flash_client if use_flash else self.client
        mdl = model or (settings.llm_flash_model if use_flash else settings.llm_model)
        if timeout:
            kwargs["timeout"] = timeout
        kwargs.pop("pipeline_id", None)

        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _produce() -> None:
            try:
                for chunk in client.generate_stream(messages, model=mdl, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _produce)
        while True:
            item = await queue.get()
            if item is None:
                break
            kind, payload = item
            if kind == "error":
                raise payload
            yield payload

    # ── 工具调用支持 ──

    # 注（轮65 评估）：isobase 上游 SDK 仅提供一次性 generate（无 token 级
    # 流式回调），因此 requirements chat 的"真流式"需要更换/封装上游流式
    # 接口后才能实现——现有 stream_chat 为"打字机式分片"而非 token 流。

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
        # B-1：消息规范 + 能力校验由 provider 协议提供（legacy 分支兜底）
        # B2/B3：协议方法缺失/能力未声明由访问器兜底（可读告警，不再 AttributeError）
        impl = _resolve_llm_provider(self.provider)
        if impl is not None:
            from clipwright.providers.base import llm_capabilities, llm_uses_system_kwarg
            caps = llm_capabilities(impl, settings.llm_model)
            if not caps.tool_calls:
                raise ValueError(
                    f"provider {self.provider} 不支持工具调用（tool_calls=false）——"
                    "请改用支持工具的 provider 或去掉工具参数"
                )
            if llm_uses_system_kwarg(impl):
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
        elif self.provider == "anthropic":
            messages = [
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

        logger.debug("LLM with_tools 启动: system=%.300s, user=%.300s, tools=%s, max_rounds=%s",
                     system_prompt[:300], user_prompt[:300],
                     json.dumps(tools, ensure_ascii=False)[:300], max_tool_rounds)
        tool_executor_is_coro = asyncio.iscoroutinefunction(tool_executor)

        for _round in range(max_tool_rounds):
            # 修复：每轮都必须携带 tools——旧实现仅首轮下发，模型按提示词
            # 顺序先调 describe_llm_mg 后，第二轮再无任何工具可调
            kw = {**base_kwargs, "tools": tools}

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
        role: str = "",
    ) -> AnthropicMessages | OpenAIChat:
        """使用指定参数构建 LLM 客户端（不依赖实例配置）。

        用于需要独立 LLM 配置的场景（如视觉识别使用不同的模型/API）。
        B-1a：委托唯一构造点 `_construct_client`（与实例路径共用缓存与协议解析）。
        B3：`role` 非空时优先采用该角色绑定的 Provider 源凭据（绑定缺失回退实参）。
        """
        p = provider or settings.llm_provider
        k = api_key or settings.llm_api_key or None
        m = model or settings.llm_model
        u = base_url or settings.llm_base_url or None
        bound = _resolve_role_binding(role)
        timeout_sec, proxy, headers = 0.0, "", None
        if bound:
            p = bound.get("provider") or p
            k = bound.get("api_key") or k
            m = bound.get("model") or m
            u = bound.get("base_url") or u
            timeout_sec = float(bound.get("timeout_sec") or 0.0)
            proxy = str(bound.get("proxy") or "")
            headers = dict(bound.get("headers") or {}) or None
        return LLMService._construct_client(
            p, api_key=k, base_url=u, model=m,
            instructions=settings.llm_instructions, max_tokens=8192, flash=False,
            timeout_sec=timeout_sec, proxy=proxy, headers=headers,
        )

    def reset(self) -> None:
        """重置客户端（重新初始化时使用）。"""
        self._client = None
        self._flash_client = None
        # 清理本实例对应的共享缓存项，保证配置热更新后下次构建新客户端
        # （B4：缓存键含传输参数指纹，故按 (provider, base_url, model, api_key)
        # 前缀匹配清理，覆盖该凭据下的全部传输变体）
        try:
            prefixes = [
                (self.provider, settings.llm_base_url or None, settings.llm_model,
                 settings.llm_api_key or None),
                (settings.llm_flash_provider or self.provider,
                 settings.llm_flash_base_url or settings.llm_base_url or None,
                 settings.llm_flash_model or settings.llm_model,
                 settings.llm_flash_api_key or settings.llm_api_key or None),
            ]
            for key in list(_client_cache):
                if key[:4] in prefixes:
                    _client_cache.pop(key, None)
        except Exception:
            pass
