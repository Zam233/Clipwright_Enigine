"""动画 Agent 的联网素材化接线（PRD web-search-fetch M2）。

现场：`_ingest_web_media` 之前，搜索结果里的图片/视频从不落盘 → "动画自己找素材"不成立。

**2026-11-02 架构收口后**：`_ingest_web_media` 只做编排与记账，具体能力全在工具/插件层
（检索 `web_search`、抽图落盘 `web_images`、单 URL 抓取 `web_fetch`）。所以本文件把打桩点
放在 `agent.call_tool` 上——不再 patch `WebSearchService`（agent 里已经没有那个 import 了）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from clipwright.agents import animation_agent as aa
from clipwright.schema.tool import ToolExecResult, ToolStatus


def _ctx(root: str, topic: str = "PCL 启动器 停止下载"):
    return SimpleNamespace(topic=topic, extra_params={"workspace_root": root}, pipeline_id="p1")


def test_ingest_web_media_adds_images_to_index(monkeypatch, tmp_path):
    """搜索返回一个媒体直链 → `web_fetch` 落盘 → agent 只记账进图片索引。"""
    local = tmp_path / "materials/web/images/a.jpg"
    local.parent.mkdir(parents=True, exist_ok=True)
    # > 8000B：太小的图会被当成站标/1x1 占位跳过（实现里的实测经验）
    local.write_bytes(b"\xff\xd8" + b"0" * 12000)

    async def _call_tool(capability: str, **kwargs):
        if capability == "web_search":
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="web_search",
                output={"results": [
                    {"title": "启动器截图", "url": "https://example.com/a.jpg", "snippet": ""},
                    {"title": "非媒体页", "url": "https://example.com/page", "snippet": ""},
                ]},
            )
        if capability == "web_fetch":
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="web_fetch",
                output={
                    "content": "", "kind": "image", "local_path": str(local),
                    "bytes": 12000, "license": "unknown（需人工确认授权）",
                },
            )
        if capability == "web_images":
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name="web_images", output={"images": []},
            )
        return ToolExecResult(status=ToolStatus.NOT_FOUND, tool_name=capability, output={})

    # 素材源直链先试：让它空手而归，才会走"联网"分支
    from clipwright.material import MaterialRegistry

    monkeypatch.setattr(MaterialRegistry, "search", AsyncMock(return_value=[]))

    agent = aa.AnimationAgent()
    agent._image_index = []
    # 挂在实例上（类上挂普通函数会被绑成 method，多一个 self 参数）
    agent.call_tool = _call_tool

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
