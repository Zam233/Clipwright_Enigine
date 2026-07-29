"""Tests for T10 — pipeline audio_config wiring into AudioInput."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.schema.agent import AgentContext, AgentDecision, AudioInput
from clipwright.schema.timeline import Timeline


def _make_ctx(**extra: object) -> AgentContext:
    return AgentContext(
        pipeline_id="p_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={**extra},
    )


@pytest.mark.asyncio
async def test_audio_config_passed_through() -> None:
    """audio_config from data dict reaches AudioInput.audio_config."""
    orch = PipelineOrchestrator()

    received: dict = {}
    original_execute = orch._agents["audio"].execute

    async def spy_execute(input_data: AudioInput, ctx: AgentContext) -> object:
        received["audio_config"] = input_data.audio_config
        return await original_execute(input_data, ctx)

    orch._agents["audio"].execute = spy_execute

    ctx = _make_ctx()
    result = await orch._dispatch_agent(
        "audio",
        {"timeline": Timeline(), "audio_config": {"voice_id": "vX", "auto_dub": True}},
        ctx,
    )

    assert received["audio_config"]["voice_id"] == "vX"
    assert received["audio_config"]["auto_dub"] is True


@pytest.mark.asyncio
async def test_missing_audio_config_defaults_to_empty() -> None:
    """When data dict has no audio_config, AudioInput.audio_config == {}."""
    orch = PipelineOrchestrator()

    received: dict = {}
    original_execute = orch._agents["audio"].execute

    async def spy_execute(input_data: AudioInput, ctx: AgentContext) -> object:
        received["audio_config"] = input_data.audio_config
        return await original_execute(input_data, ctx)

    orch._agents["audio"].execute = spy_execute

    ctx = _make_ctx()
    result = await orch._dispatch_agent("audio", {"timeline": Timeline()}, ctx)

    assert received["audio_config"] == {}


@pytest.mark.asyncio
async def test_animation_dispatch_not_affected() -> None:
    """Animation dispatch still passes visual_config — no regression."""
    orch = PipelineOrchestrator()

    received: dict = {}
    original_execute = orch._agents["animation"].execute

    async def spy_execute(input_data, ctx: AgentContext) -> object:
        received["visual_config"] = getattr(input_data, "visual_config", "MISSING")
        from clipwright.schema.agent import AnimationOutput
        return AnimationOutput(decision=AgentDecision.PASS, timeline=input_data.timeline)

    orch._agents["animation"].execute = spy_execute

    ctx = _make_ctx()
    tl = Timeline()
    result = await orch._dispatch_agent(
        "animation",
        {"timeline": tl, "visual_config": {"a": 1}},
        ctx,
    )

    assert received["visual_config"] == {"a": 1}


def test_audio_config_in_source() -> None:
    """Verify 'audio_config' string is present in pipeline.py source."""
    import inspect
    import clipwright.services.pipeline as p
    src = inspect.getsource(p)
    assert "audio_config" in src
    assert "voice_clone_model_id" in src
    assert "auto_dub" in src
