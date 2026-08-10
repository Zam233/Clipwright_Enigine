"""E10: MaterialAgent._validate_via_vision_llm URL→score 缓存（上限 512）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.material_agent import (
    MaterialAgent,
    clear_material_vision_cache,
    _VISION_SCORE_CACHE,
    _VISION_SCORE_CACHE_MAX,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_material_vision_cache()
    yield
    clear_material_vision_cache()


def _make_agent() -> MaterialAgent:
    return MaterialAgent.__new__(MaterialAgent)


class TestMaterialVisionCache:
    @pytest.mark.asyncio
    async def test_same_url_tool_called_once(self) -> None:
        agent = _make_agent()
        asset = SimpleNamespace(url="http://x/v1.mp4", local_path="")
        tool = AsyncMock(return_value=SimpleNamespace(output={"score": 0.9, "extraction_method": "x", "frames_analyzed": 3}))
        with patch("clipwright.tool.registry.ToolRegistry.execute", tool):
            s1 = await agent._validate_via_vision_llm(asset, "t", [], "d")
            s2 = await agent._validate_via_vision_llm(asset, "t", [], "d")

        assert s1 == 0.9 and s2 == 0.9
        assert tool.call_count == 1

    @pytest.mark.asyncio
    async def test_different_url_reruns(self) -> None:
        agent = _make_agent()
        tool = AsyncMock(return_value=SimpleNamespace(output={"score": 0.9, "extraction_method": "x", "frames_analyzed": 3}))
        with patch("clipwright.tool.registry.ToolRegistry.execute", tool):
            await agent._validate_via_vision_llm(SimpleNamespace(url="http://x/a.mp4", local_path=""), "t", [], "d")
            await agent._validate_via_vision_llm(SimpleNamespace(url="http://x/b.mp4", local_path=""), "t", [], "d")

        assert tool.call_count == 2

    @pytest.mark.asyncio
    async def test_dict_asset_uses_url(self) -> None:
        agent = _make_agent()
        tool = AsyncMock(return_value=SimpleNamespace(output={"score": 0.8, "extraction_method": "x", "frames_analyzed": 3}))
        with patch("clipwright.tool.registry.ToolRegistry.execute", tool):
            s1 = await agent._validate_via_vision_llm({"url": "http://x/c.mp4"}, "t", [], "d")
            s2 = await agent._validate_via_vision_llm({"url": "http://x/c.mp4"}, "t", [], "d")

        assert s1 == 0.8 and s2 == 0.8
        assert tool.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_failure_returns_0_5_and_not_poisoned(self) -> None:
        agent = _make_agent()
        tool = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("clipwright.tool.registry.ToolRegistry.execute", tool):
            s = await agent._validate_via_vision_llm(SimpleNamespace(url="http://x/fail.mp4", local_path=""), "t", [], "d")

        assert s == 0.5
        assert len(_VISION_SCORE_CACHE) == 0  # 失败不写缓存

    @pytest.mark.asyncio
    async def test_cache_bounded(self, monkeypatch) -> None:
        monkeypatch.setattr("clipwright.agents.material_agent._VISION_SCORE_CACHE_MAX", 2)
        agent = _make_agent()
        tool = AsyncMock(return_value=SimpleNamespace(output={"score": 0.7, "extraction_method": "x", "frames_analyzed": 3}))
        with patch("clipwright.tool.registry.ToolRegistry.execute", tool):
            for i in range(5):
                await agent._validate_via_vision_llm(SimpleNamespace(url=f"http://x/{i}.mp4", local_path=""), "t", [], "d")

        assert len(_VISION_SCORE_CACHE) <= 2
