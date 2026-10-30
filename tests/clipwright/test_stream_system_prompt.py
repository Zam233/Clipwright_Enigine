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
