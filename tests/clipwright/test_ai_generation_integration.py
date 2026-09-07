"""AI 生成产物入轨 + 规划感知集成测试（轮次 61 / docs/ai-generation-integration-plan.md）。

覆盖：工具可用性三态与 list_agent_callable 过滤、生成历史索引与素材源检索、
AnimationAgent 素材库主动搜图、需求服务 AI 能力概览与 asset_ratio 渲染。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

import ai_image_gen.main as img_mod  # noqa: E402
import ai_music_gen.main as mus_mod  # noqa: E402
import ai_video_gen.main as vid_mod  # noqa: E402

from clipwright.material.registry import MaterialRegistry  # noqa: E402
from clipwright.plugins.generated_source import (  # noqa: E402
    generated_image_entries,
    generated_history_path,
    load_generated_history,
    make_generated_source,
    record_generated,
)
from clipwright.tool.registry import ToolRegistry  # noqa: E402

_ENV_KEYS = ("ARK_API_KEY", "OPENAI_API_KEY", "FLUX_API_KEY",
             "KLING_API_KEY", "RUNWAY_API_KEY",
             "VOLC_ACCESS_KEY", "VOLC_SECRET_KEY",
             "VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY",
             "SUNO_API_KEY")


@pytest.fixture(autouse=True)
def _isolated_env_and_registry(tmp_path, monkeypatch):
    """generated.json 等产物隔离到临时目录；恢复全局注册表与 env。"""
    monkeypatch.chdir(tmp_path)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    saved_sources = dict(MaterialRegistry._sources)
    yield
    MaterialRegistry._sources.clear()
    MaterialRegistry._sources.update(saved_sources)


# ── D1: 工具可用性三态 ─────────────────────────────────


def test_image_tool_availability_matrix(monkeypatch):
    assert img_mod.AIImageGenTool(provider="volcengine").is_available() is False
    assert img_mod.AIImageGenTool(provider="volcengine", api_key="k").is_available() is True
    monkeypatch.setenv("ARK_API_KEY", "env")
    assert img_mod.AIImageGenTool(provider="volcengine").is_available() is True
    assert img_mod.AIImageGenTool(provider="dalle", api_key="k").is_available() is True
    assert img_mod.AIImageGenTool(provider="dalle").is_available() is False
    assert img_mod.AIImageGenTool(provider="flux").is_available() is False
    assert img_mod.AIImageGenTool(provider="local").is_available() is True  # SD 默认地址


def test_video_tool_availability_matrix(monkeypatch):
    assert vid_mod.AIVideoGenTool(provider="volcengine").is_available() is False
    assert vid_mod.AIVideoGenTool(provider="volcengine", api_key="k").is_available() is True
    monkeypatch.setenv("ARK_API_KEY", "env")
    assert vid_mod.AIVideoGenTool(provider="volcengine").is_available() is True
    assert vid_mod.AIVideoGenTool(provider="kling").is_available() is False
    assert vid_mod.AIVideoGenTool(provider="runway").is_available() is False
    assert vid_mod.AIVideoGenTool(provider="runway", api_key="k").is_available() is True


def test_music_tool_availability_matrix(monkeypatch):
    # volcengine 需要 AK/SK 成对
    assert mus_mod.AIMusicGenTool().is_available() is False
    assert mus_mod.AIMusicGenTool(access_key="ak").is_available() is False
    assert mus_mod.AIMusicGenTool(access_key="ak", secret_key="sk").is_available() is True
    monkeypatch.setenv("VOLCENGINE_ACCESS_KEY", "ak")
    monkeypatch.setenv("VOLCENGINE_SECRET_KEY", "sk")
    assert mus_mod.AIMusicGenTool().is_available() is True  # VOLCENGINE_ 前缀兜底
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY")
    monkeypatch.setenv("VOLC_ACCESS_KEY", "ak")
    assert mus_mod.AIMusicGenTool().is_available() is True  # VOLC_ 前缀兜底
    assert mus_mod.AIMusicGenTool(provider="suno", api_key="k").is_available() is True
    assert mus_mod.AIMusicGenTool(provider="suno").is_available() is False


def test_unconfigured_tools_hidden_from_agent_callable(monkeypatch):
    """核心要求：只有可用（已配置凭据）的生成工具才进入 Agent 的 function schemas。"""
    unconfigured = img_mod.AIImageGenTool(provider="volcengine")
    monkeypatch.setattr(ToolRegistry, "_tools", {"ai_image_generate": unconfigured})
    names = [t.name for t in ToolRegistry.list_agent_callable()]
    assert "ai_image_generate" not in names

    configured = img_mod.AIImageGenTool(provider="volcengine", api_key="k")
    monkeypatch.setattr(ToolRegistry, "_tools", {"ai_image_generate": configured})
    names = [t.name for t in ToolRegistry.list_agent_callable()]
    assert "ai_image_generate" in names


# ── D2: 生成历史索引 + 素材源检索 ───────────────────────


def test_record_and_load_history():
    record_generated("ai_image_gen", prompt="赛博朋克城市夜景", type="image",
                     path="PluginData/assets/a.png", resolution="1280x720")
    record_generated("ai_image_gen", prompt="星空延时", type="image",
                     path="PluginData/assets/b.png")
    hist = load_generated_history("ai_image_gen")
    assert len(hist) == 2
    assert hist[0]["id"] == "a"  # id 自动取文件名 stem
    assert "created_at" in hist[0]
    assert generated_history_path("ai_image_gen").exists()


def test_history_corrupt_file_rebuilds():
    p = generated_history_path("ai_music_gen")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json{", encoding="utf-8")
    record_generated("ai_music_gen", prompt="爵士", type="audio", path="x.mp3")
    assert len(load_generated_history("ai_music_gen")) == 1


def test_generated_source_search_and_miss():
    Path("PluginData/assets").mkdir(parents=True, exist_ok=True)
    Path("PluginData/assets/night.png").write_bytes(b"\x89PNG")
    record_generated("ai_image_gen", prompt="赛博朋克城市夜景 霓虹雨夜", type="image",
                     path="PluginData/assets/night.png")
    record_generated("ai_image_gen", prompt="已清理的图", type="image",
                     path="PluginData/assets/gone.png")  # 文件不存在 → 跳过

    src = make_generated_source("ai_image_gen", "AI 生成图片", "image")
    hits = asyncio.run(src.search("赛博朋克 城市夜景", top_k=5))
    assert len(hits) == 1
    asset, score = hits[0]
    assert Path(asset.local_path).exists()
    assert asset.metadata.get("ai_generated") is True
    assert asset.type == "image"
    assert 0.70 <= score <= 0.92
    assert src.source_id == "ai_image_gen"
    assert src.source_name == "AI 生成图片"

    # 无关 query 不命中；空历史源返回 []
    assert asyncio.run(src.search("美食探店 火锅", top_k=5)) == []
    empty = make_generated_source("ai_video_gen", "AI 生成视频", "video")
    assert asyncio.run(empty.search("任意", top_k=5)) == []
    assert asyncio.run(empty.count()) == 0


def test_generated_source_registers_into_registry():
    src = make_generated_source("ai_music_gen", "AI 生成音乐", "audio")
    MaterialRegistry.register(src, plugin_id="ai_music_gen")
    listed = MaterialRegistry.list()
    assert any(item.get("id") == "ai_music_gen" and item.get("name") == "AI 生成音乐"
               for item in listed)
    # 经注册表检索走同一 fan-out（AudioAgent BGM 链路同款调用）
    assert asyncio.run(MaterialRegistry.search("轻快 BGM", top_k_per_source=3)) == []


def test_image_plugin_success_records_history(monkeypatch):
    """生成成功 → generated.json 落盘 → 素材源可检索（链路闭环）。"""
    import ai_image_gen.main as img
    from tests.clipwright.test_volcengine_gen_plugins import FakeClient, FakeResp

    monkeypatch.setattr(img.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(img, "assert_public_url", lambda u: None)
    FakeClient.entries = [
        ("POST", "/images/generations",
         FakeResp({"data": [{"url": "https://cdn.example.com/x.png"}]})),
        ("GET", "https://cdn.example.com/x.png", FakeResp(content=b"\x89PNG")),
    ]
    tool = img.AIImageGenTool(provider="volcengine", api_key="k")
    out = asyncio.run(tool.execute(prompt="赛博朋克城市夜景 霓虹雨夜", width=1024, height=576))
    assert out.get("success") is True

    MaterialRegistry.register(
        make_generated_source("ai_image_gen", "AI 生成图片", "image"), plugin_id="ai_image_gen")
    res = asyncio.run(MaterialRegistry.search("赛博朋克 城市夜景", top_k_per_source=5))
    assert res and res[0].source_name == "AI 生成图片"
    assert Path(res[0].asset.local_path).exists()


def test_generated_image_entries_shape():
    Path("PluginData/assets").mkdir(parents=True, exist_ok=True)
    Path("PluginData/assets/ok.png").write_bytes(b"\x89PNG")
    record_generated("ai_image_gen", prompt="赛博朋克城市夜景", type="image",
                     path="PluginData/assets/ok.png")
    record_generated("ai_image_gen", prompt="已清理", type="image",
                     path="PluginData/assets/missing.png")
    entries = generated_image_entries()
    assert len(entries) == 1
    assert entries[0]["path"].endswith("ok.png")
    assert entries[0]["tags"]
    assert entries[0]["description"].startswith("赛博朋克")


# ── D5: AnimationAgent 素材库主动搜图 ───────────────────


def test_animation_agent_searches_library_images():
    from clipwright.agents.animation_agent import AnimationAgent

    class _Asset:
        def __init__(self, t, local_path=None, url=None, tags=None, title=""):
            self.type = t
            self.local_path = local_path
            self.url = url
            self.tags = tags or []
            self.title = title

    class _Result:
        def __init__(self, asset):
            self.asset = asset

    class _FakeRegistry:
        @staticmethod
        def list():
            return [{"id": "s", "name": "s"}]

        @staticmethod
        async def search(query, top_k_per_source=4, **kw):
            assert "机甲" in query
            return [
                _Result(_Asset("image", local_path="PluginData/assets/a.png",
                               tags=["机甲"], title="机甲图")),
                _Result(_Asset("image", url="http://127.0.0.1/x.png", title="内网图")),
                _Result(_Asset("video", local_path="v.mp4")),
            ]

    import clipwright.material.registry as reg_mod
    saved = reg_mod.MaterialRegistry
    reg_mod.MaterialRegistry = _FakeRegistry
    try:
        entries = asyncio.run(AnimationAgent()._search_library_images(topic="机甲大战"))
    finally:
        reg_mod.MaterialRegistry = saved

    # 内网图下载被 SSRF 校验拒绝 → 跳过；video 类型 → 过滤
    assert len(entries) == 1
    assert entries[0]["path"] == "PluginData/assets/a.png"
    assert entries[0]["tags"] == ["机甲"]
    assert entries[0]["description"] == "机甲图"


def test_animation_agent_search_skips_when_registry_empty():
    from clipwright.agents.animation_agent import AnimationAgent

    class _EmptyRegistry:
        @staticmethod
        def list():
            return []

    import clipwright.material.registry as reg_mod
    saved = reg_mod.MaterialRegistry
    reg_mod.MaterialRegistry = _EmptyRegistry
    try:
        entries = asyncio.run(AnimationAgent()._search_library_images(topic="任意"))
    finally:
        reg_mod.MaterialRegistry = saved
    assert entries == []


# ── D4: 需求服务 AI 能力概览 + asset_ratio ──────────────


def test_ai_generation_overview_empty_when_unconfigured():
    from clipwright.services import requirements_service as rs
    assert rs._ai_generation_overview() == ""


def test_ai_generation_overview_lists_available(monkeypatch):
    from clipwright.services import requirements_service as rs
    configured = img_mod.AIImageGenTool(provider="volcengine", api_key="k")
    monkeypatch.setattr(
        ToolRegistry, "get",
        staticmethod(lambda name: configured if name == "ai_image_generate" else None))
    overview = rs._ai_generation_overview()
    assert "AI 生成能力" in overview and "图片生成" in overview
    assert "视频生成" not in overview  # 未配置的不呈现


def test_ai_generation_overview_tool_unavailable(monkeypatch):
    from clipwright.services import requirements_service as rs
    unconfigured = img_mod.AIImageGenTool(provider="volcengine")  # 无 key
    monkeypatch.setattr(
        ToolRegistry, "get",
        staticmethod(lambda name: unconfigured if name == "ai_image_generate" else None))
    assert rs._ai_generation_overview() == ""


def test_structure_brief_renders_ai_generated_ratio():
    from clipwright.agents.structure_agent import StructureAgent
    text = StructureAgent._build_brief_context(
        {"asset_ratio": {"footage": "30%", "mg": "60%", "ai_generated": "10%"}}, None)
    assert "AI 生成 10%" in text
    # 空值不显示（additive）
    text2 = StructureAgent._build_brief_context(
        {"asset_ratio": {"footage": "30%", "mg": "60%"}}, None)
    assert "AI 生成" not in text2


def test_brief_prompt_template_has_ai_generated_slot():
    from clipwright.services import requirements_service as rs
    assert "ai_generated" in rs.CREATIVE_BRIEF_SYSTEM
