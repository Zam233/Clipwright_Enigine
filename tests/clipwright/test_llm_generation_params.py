"""B4（2026-10-28 PRD）：逐模型 generation 参数。

三件可证伪的事：
1. 模型项里的 `generation` 会被校验/归一化（越界截断、非法丢弃），且能读回；
2. 真实调用路径（generate / with_tools 每一轮 / stream_generate）都使用同一份
   解析结果；显式 kwargs 优先于模型配置；
3. provider 不支持的参数**不下发**，并登记 `unsupported_parameter` 降级。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.config import settings
from clipwright.services import provider_sources as ps
from clipwright.services.llm import LLMResponse, LLMService
from clipwright.services.provider_sources import (
    GENERATION_LIMITS,
    generation_for,
    normalize_generation,
)


@pytest.fixture(autouse=True)
def _isolate_sources(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)


def _src(provider: str = "openai", models: list[Any] | None = None) -> str:
    src = ps.create_source({
        "name": f"src-{provider}", "kind": "llm", "provider": provider,
        "base_url": f"https://{provider}.example.com",
        "api_keys": ["sk-gen-0001"], "models": models or [],
    })
    return str(src["id"])


# ── 配置解析与校验 ───────────────────────────────────────────


def test_normalize_generation_clamps_and_drops_invalid():
    gen = normalize_generation({
        "temperature": 5,          # 上限 2 → 截断
        "top_p": "oops",           # 非数值 → 丢弃
        "max_tokens": 1234.7,      # 取整
        "unknown_key": 1,          # 未知键 → 丢弃
        "reasoning_effort": "high",  # 不属于生成参数
    })
    assert gen["temperature"] == 2.0
    assert "top_p" not in gen
    assert gen["max_tokens"] == 1235
    assert "unknown_key" not in gen and "reasoning_effort" not in gen


def test_normalize_generation_limits_are_declared():
    assert set(GENERATION_LIMITS) == {"temperature", "top_p", "max_tokens"}


def test_model_item_keeps_generation_and_reads_back():
    _src("openai", [{"id": "m1", "generation": {"temperature": 0.3, "max_tokens": 900}}])
    assert generation_for("openai", "m1") == {"temperature": 0.3, "max_tokens": 900}
    # 未配置的模型 → 空（调用方沿用默认）
    assert generation_for("openai", "other") == {}
    assert generation_for("", "m1") == {}


def test_model_without_generation_has_no_extra_key():
    src = ps.create_source({
        "name": "plain", "kind": "llm", "provider": "anthropic",
        "base_url": "https://a.example.com", "api_keys": ["k"], "models": ["claude-3-haiku"],
    })
    item = ps.get_source(src["id"], public=False)["models"][0]
    assert "generation" not in item, "没配 generation 时不要写空字段"


# ── 统一解析器 ──────────────────────────────────────────────


def _svc_with_captured_generate(monkeypatch) -> tuple[LLMService, list[dict[str, Any]]]:
    """把 client.generate 换成记录入参的桩，返回 (service, calls)。"""
    calls: list[dict[str, Any]] = []

    class _Client:
        def generate(self, **kwargs: Any) -> LLMResponse:
            calls.append(dict(kwargs))
            return LLMResponse(content="ok", success=True, status_code=200)

        def ask(self, **kwargs: Any) -> LLMResponse:  # 未使用
            return LLMResponse(content="", success=True, status_code=200)

    monkeypatch.setattr(LLMService, "_routed_client_and_model",
                        lambda self, use_flash=False: (_Client(), None))
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "llm_model", "m1", raising=False)
    return LLMService(), calls


def test_generate_uses_model_generation_config(monkeypatch):
    _src("openai", [{"id": "m1", "generation": {"temperature": 0.2, "max_tokens": 512}}])
    svc, calls = _svc_with_captured_generate(monkeypatch)

    asyncio.run(svc.generate(messages=[{"role": "user", "content": "hi"}]))
    assert calls[-1]["temperature"] == 0.2
    assert calls[-1]["max_tokens"] == 512


def test_explicit_kwargs_override_model_generation(monkeypatch):
    _src("openai", [{"id": "m1", "generation": {"temperature": 0.2}}])
    svc, calls = _svc_with_captured_generate(monkeypatch)

    asyncio.run(svc.generate(messages=[{"role": "user", "content": "hi"}], temperature=1.1))
    assert calls[-1]["temperature"] == 1.1


def test_every_tool_round_uses_same_generation(monkeypatch):
    """工具循环首轮与后续轮参数必须一致（旧实现只首轮生效就会丢）。"""
    _src("openai", [{"id": "m1", "generation": {"temperature": 0.35, "top_p": 0.8}}])
    svc, calls = _svc_with_captured_generate(monkeypatch)

    rounds = {"n": 0}

    class _Client:
        def generate(self, **kwargs: Any) -> LLMResponse:
            calls.append(dict(kwargs))
            rounds["n"] += 1
            if rounds["n"] == 1:
                return LLMResponse(
                    content="先说说", success=True, status_code=200,
                    tool_calls=[SimpleNamespace(id="c1", name="noop", arguments="{}")],
                )
            return LLMResponse(content="done", success=True, status_code=200)

    monkeypatch.setattr(LLMService, "_routed_client_and_model",
                        lambda self, use_flash=False: (_Client(), None))

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}

    asyncio.run(svc.with_tools(
        system_prompt="s", user_prompt="u", tool_executor=executor,
        tools=[{"type": "function", "function": {"name": "noop"}}],
        max_tool_rounds=3,
    ))
    assert len(calls) >= 2
    assert all(c.get("temperature") == 0.35 for c in calls)
    assert all(c.get("top_p") == 0.8 for c in calls)


def test_stream_generate_uses_model_generation(monkeypatch):
    _src("openai", [{"id": "m1", "generation": {"temperature": 0.9}}])
    seen: list[dict[str, Any]] = []

    class _StreamClient:
        def generate_stream(self, messages, model=None, **kwargs):  # noqa: ANN001
            seen.append({**kwargs, "model": model})
            yield LLMResponse(content="x", success=True, status_code=200)

    svc = LLMService()
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "llm_model", "m1", raising=False)
    monkeypatch.setattr(LLMService, "client", _StreamClient(), raising=False)
    monkeypatch.setattr(LLMService, "flash_client", _StreamClient(), raising=False)

    async def _drain() -> None:
        async for _ in svc.stream_generate([{"role": "user", "content": "hi"}]):
            pass

    asyncio.run(_drain())
    assert seen and seen[0]["temperature"] == 0.9


def test_unsupported_parameter_is_dropped_with_degradation(monkeypatch):
    """provider 声明不支持 top_p → 不下发，且登记 unsupported_parameter。"""
    from clipwright.services import degradation

    _src("openai", [{"id": "m1", "generation": {"temperature": 0.4, "top_p": 0.5}}])
    svc, calls = _svc_with_captured_generate(monkeypatch)

    from clipwright.providers.base import ProviderCapabilities

    class _Limited:
        name = "openai"

        def capabilities(self, model: str) -> ProviderCapabilities:
            return ProviderCapabilities(
                streaming=True, tool_calls=True,
                generation_params=("temperature", "max_tokens"),
            )

    monkeypatch.setattr("clipwright.services.llm._resolve_llm_provider", lambda _p: _Limited())

    sink: list[Any] = []
    with degradation.degradation_scope(sink):
        asyncio.run(svc.generate(messages=[{"role": "user", "content": "hi"}],
                                 top_p=0.5))

    assert "top_p" not in calls[-1], "不支持的参数不许下发（不能假装生效）"
    assert calls[-1]["temperature"] == 0.4
    assert any(d.kind == "unsupported_parameter" for d in sink)
