"""动画 Agent 的联网素材化接线（PRD web-search-fetch M2）。

现场：`_ingest_web_media` 之前，搜索结果里的图片/视频从不落盘 → "动画自己找素材"不成立。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.agents import animation_agent as aa


def _ctx(root: str, topic: str = "PCL 启动器 停止下载"):
    return SimpleNamespace(topic=topic, extra_params={"workspace_root": root}, pipeline_id="p1")


def test_ingest_web_media_adds_images_to_index(monkeypatch, tmp_path):
    monkeypatch.setattr("clipwright.services.web_search.WebSearchService.is_configured",
                        lambda self: True, raising=False)

    async def _search(self, query, max_results=None):  # noqa: ANN001, ANN003
        return [
            {"title": "启动器截图", "url": "https://example.com/a.jpg", "snippet": "", "score": 0},
            {"title": "非媒体页", "url": "https://example.com/page", "snippet": "", "score": 0},
        ]

    monkeypatch.setattr("clipwright.services.web_search.WebSearchService.search", _search)

    class _Res:
        ok = True
        kind = "images"
        local_path = str(tmp_path / "materials/web/images/a.jpg")
        url = "https://example.com/a.jpg"
        title = "启动器截图"
        license = "unknown（需人工确认授权）"

        def to_public(self):
            return {"ok": True}

    async def _ingest(results, *, root, limit=3, media_only=False, client_factory=None):  # noqa: ANN001, ANN003
        assert len(results) == 1  # 只保留媒体 URL
        return [_Res()]

    monkeypatch.setattr("clipwright.services.web_assets.ingest_results", _ingest)

    agent = aa.AnimationAgent()
    agent._image_index = []
    added = asyncio.run(agent._ingest_web_media(_ctx(str(tmp_path)), topic="PCL 启动器"))
    assert added == 1
    assert agent._image_index[0]["path"].endswith("a.jpg")
    assert agent._image_index[0]["license"].startswith("unknown")


def test_ingest_web_media_skips_without_workspace(monkeypatch):
    agent = aa.AnimationAgent()
    added = asyncio.run(agent._ingest_web_media(
        SimpleNamespace(topic="x", extra_params={}, pipeline_id="p"), topic="x"))
    assert added == 0


def test_ingest_web_media_respects_flag(monkeypatch, tmp_path):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "web_assets_ingest", False, raising=False)
    agent = aa.AnimationAgent()
    added = asyncio.run(agent._ingest_web_media(_ctx(str(tmp_path)), topic="x"))
    assert added == 0
