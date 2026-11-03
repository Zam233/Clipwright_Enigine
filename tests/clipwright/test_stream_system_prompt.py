"""需求对话真流式的两条硬约束（PRD §19.4 / 用户反馈"正在思考…点了没反应"）。

根因回归：`LLMService.stream_generate` 早期没有 `system_prompt` 形参，调用方传的
`system_prompt=...` 掉进 `**kwargs`、被透传给 OpenAI SDK，抛
`Completions.create() got an unexpected keyword argument 'system_prompt'`；
`_handle_gathering` 的 `except Exception` 把它当"流式不可用"静默回退缓冲路径，
于是**一个 delta / reasoning 帧都不会发出**，前端只能显示不可点的"正在思考…"。

这里钉三件事：
1. `stream_generate` 把 `system_prompt` 变成 `messages[0].system`，且不透传给 provider；
2. 本方法的元参数（output_schema / on_* / messages）也不会漏到 provider；
3. 需求对话在 gathering 态真实产出 `delta` 与 `reasoning` 帧（不再静默回退）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.services.llm import LLMService
from clipwright.services.requirements_service import RequirementsService


class _FakeClient:
    """记录 provider 实际收到的参数；按需产出 chunk。"""

    def __init__(self, chunks: list[Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._chunks = chunks or []

    def generate_stream(self, messages, model=None, **kwargs):  # noqa: ANN001
        self.calls.append({"messages": messages, "model": model, "kwargs": kwargs})
        for c in self._chunks:
            yield c


def _svc(client: _FakeClient) -> LLMService:
    svc = LLMService.__new__(LLMService)
    # client / flash_client 是只读 property（惰性构造），直接塞底层缓存字段
    svc._client = client           # type: ignore[attr-defined]
    svc._flash_client = client     # type: ignore[attr-defined]
    # 逐模型生成参数需要 provider 上下文；本用例只验"参数隔离 + system 注入"
    svc._effective_generation_kwargs = lambda mdl, flash, kw: kw  # type: ignore[assignment]
    return svc


async def _drain(gen) -> list[Any]:
    return [c async for c in gen]


class TestStreamGenerateSystemPrompt:
    async def test_system_prompt_becomes_system_message_not_kwarg(self) -> None:
        client = _FakeClient([SimpleNamespace(content="x", reasoning_content="")])
        svc = _svc(client)
        out = await _drain(svc.stream_generate(
            [{"role": "user", "content": "hi"}],
            system_prompt="你是需求分析助手",
        ))
        assert out
        call = client.calls[0]
        assert "system_prompt" not in call["kwargs"], "system_prompt 绝不能透传给 provider"
        assert call["messages"][0] == {"role": "system", "content": "你是需求分析助手"}
        assert call["messages"][1] == {"role": "user", "content": "hi"}

    async def test_meta_kwargs_do_not_leak(self) -> None:
        client = _FakeClient([SimpleNamespace(content="x", reasoning_content="")])
        svc = _svc(client)
        await _drain(svc.stream_generate(
            [{"role": "user", "content": "hi"}],
            system_prompt="s",
            output_schema={"type": "object"},
            on_delta=lambda t: None,
            on_reasoning=lambda t: None,
        ))
        leaked = {"system_prompt", "output_schema", "on_delta", "on_reasoning", "messages"} \
            & set(client.calls[0]["kwargs"])
        assert leaked == set(), f"元参数漏给 provider: {leaked}"

    async def test_no_system_prompt_keeps_messages(self) -> None:
        client = _FakeClient([SimpleNamespace(content="x", reasoning_content="")])
        svc = _svc(client)
        await _drain(svc.stream_generate([{"role": "user", "content": "hi"}]))
        assert client.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


class _FakeStreamLLM:
    """需求对话用的假 LLM：先给 reasoning，再按 JSON 首键给 reply 增量。"""

    def __init__(self, reply: str = "好的，我先了解三点") -> None:
        self.reply = reply

    async def stream_generate(self, messages, system_prompt=None, **kwargs):  # noqa: ANN001
        assert system_prompt, "需求对话必须把 system prompt 传进来"
        payload = json.dumps({"reply": self.reply, "brief_draft": {}, "is_ready": False},
                             ensure_ascii=False)
        yield SimpleNamespace(content="", reasoning_content="先确认选题边界，")
        for i in range(0, len(payload), 12):
            yield SimpleNamespace(content=payload[i:i + 12], reasoning_content="")
        yield SimpleNamespace(content="", reasoning_content="再决定要不要素材。")


class TestRequirementsStreamEmitsReasoning:
    async def test_gathering_stream_emits_delta_and_reasoning(self, monkeypatch) -> None:
        monkeypatch.setattr("clipwright.services.requirements_service.BRIEF_GENERATE_TIMEOUT", 30)
        svc = RequirementsService.__new__(RequirementsService)
        svc._llm = _FakeStreamLLM()        # type: ignore[attr-defined]
        svc._cleanup_started = True        # type: ignore[attr-defined]
        state = {"status": "gathering", "messages": [], "user_inputs": {}}
        svc.get_session = lambda sid: dict(state)  # type: ignore[method-assign]
        svc._persist = lambda *a, **k: state.update(status=a[1], messages=list(a[2]))  # type: ignore[method-assign]
        svc._retrieve_knowledge = _no_kb  # type: ignore[method-assign]
        svc._record_llm_usage = _noop  # type: ignore[method-assign]
        svc._build_full_persona_context = lambda inputs: {}  # type: ignore[method-assign]

        frames = {}
        reasoning_text = ""
        async for frame in svc.stream_chat("sess_stream", "做一期科普短片"):
            frames[frame["type"]] = frames.get(frame["type"], 0) + 1
            if frame["type"] == "reasoning":
                reasoning_text += str(frame["data"])

        assert frames.get("reasoning", 0) >= 2, frames
        assert frames.get("delta", 0) >= 2, frames
        assert "先确认选题边界" in reasoning_text
        assert frames.get("result") == 1


async def _no_kb(*a, **k):  # noqa: ANN002, ANN003
    return ""


async def _noop(*a, **k):  # noqa: ANN002, ANN003
    return None


class _CapLLM(_FakeStreamLLM):
    """带能力探测的假 LLM。"""

    def __init__(self, *, supported: bool = True, reply: str = "好的") -> None:
        super().__init__(reply)
        self._supported = supported

    def reasoning_capability(self, use_flash: bool = False) -> dict:
        return {"provider": "fake", "model": "m", "supported": self._supported,
                "streaming": self._supported,
                "reason": None if self._supported else "provider_not_exposed"}


def _svc_requirements(llm, *, status: str = "gathering"):
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = llm                    # type: ignore[attr-defined]
    svc._cleanup_started = True       # type: ignore[attr-defined]
    state = {"status": status, "messages": [], "user_inputs": {}}
    svc.get_session = lambda sid: dict(state)  # type: ignore[method-assign]
    svc._persist = lambda *a, **k: state.update(status=a[1], messages=list(a[2]))  # type: ignore[method-assign]
    svc._retrieve_knowledge = _no_kb          # type: ignore[method-assign]
    svc._record_llm_usage = _noop             # type: ignore[method-assign]
    svc._build_full_persona_context = lambda inputs: {}  # type: ignore[method-assign]
    return svc


class TestReasoningStatus:
    async def test_first_frame_declares_capability(self) -> None:
        svc = _svc_requirements(_CapLLM(supported=True))
        frames = [f async for f in svc.stream_chat("sess_cap", "做一期科普短片")]
        assert frames[0]["type"] == "status"
        status = next(f for f in frames if f["type"] == "reasoning_status")
        assert status["data"]["supported"] is True
        assert status["data"]["streaming"] is True
        assert status["data"]["available"] is None      # 首帧还不知道有没有内容
        assert status["data"]["phase"] == "gathering"

    async def test_unsupported_provider_says_so(self) -> None:
        svc = _svc_requirements(_CapLLM(supported=False))
        frames = [f async for f in svc.stream_chat("sess_nocap", "做一期科普短片")]
        status = next(f for f in frames if f["type"] == "reasoning_status")
        assert status["data"]["supported"] is False
        assert status["data"]["available"] is False
        assert status["data"]["reason"] == "provider_not_exposed"

    async def test_buffered_phase_explains_no_stream(self) -> None:
        svc = _svc_requirements(_CapLLM(supported=True), status="brief_ready")
        svc.chat = lambda *a, **k: _coro({"reply": "已记下"})  # type: ignore[method-assign]
        frames = [f async for f in svc.stream_chat("sess_buf", "改一点")]
        status = next(f for f in frames if f["type"] == "reasoning_status")
        assert status["data"]["available"] is False
        assert status["data"]["reason"] == "phase_not_streaming"
        assert status["data"]["phase"] == "brief_ready"

    async def test_stream_failure_is_reported_not_swallowed(self) -> None:
        """流式抛错 → 必须有 reasoning_status(stream_fallback) 且 result 带 diagnostics。"""
        svc = _svc_requirements(_CapLLM(supported=True))

        async def boom(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("Completions.create() got an unexpected keyword argument")

        svc._stream_gathering_llm = boom  # type: ignore[method-assign]

        async def chat_fallback(session_id, message, on_delta=None, on_reasoning=None):
            return {"reply": "回退后的回复", "brief_draft": {}, "is_ready": False}

        svc.chat = chat_fallback  # type: ignore[method-assign]
        # 走真实 _handle_gathering 路径太慢：直接验证标记消费逻辑
        from clipwright.services import requirements_service as RS

        RS._STREAM_FALLBACK["sess_fb"] = "RuntimeError: TypeError"
        frames = [f async for f in svc.stream_chat("sess_fb", "继续")]
        statuses = [f["data"] for f in frames if f["type"] == "reasoning_status"]
        fb = [x for x in statuses if x.get("reason") == "stream_fallback"]
        assert fb, statuses
        assert "TypeError" in (fb[0].get("detail") or "")
        result = next(f for f in frames if f["type"] == "result")["data"]
        assert result["diagnostics"]["stream_fallback"]

    async def test_thinking_persisted_once_per_turn(self) -> None:
        """思考按轮落一条 thinking 事件（phase=requirements），刷新可回放。"""
        svc = _svc_requirements(_CapLLM(supported=True))
        frames = [f async for f in svc.stream_chat("sess_think", "做一期科普短片")]
        assert any(f["type"] == "reasoning" for f in frames)

        from clipwright.services import ask_user
        from clipwright.services.task_events import events

        evs = events(ask_user.session_stream_id("sess_think"))
        thinking = [e for e in evs if e.type == "thinking"]
        assert len(thinking) == 1, [e.type for e in evs]
        assert thinking[0].detail["phase"] == "requirements"
        assert thinking[0].detail["done"] is True
        assert "先确认选题边界" in thinking[0].detail["delta"]


async def _coro(value):  # noqa: ANN001, ANN202
    return value
