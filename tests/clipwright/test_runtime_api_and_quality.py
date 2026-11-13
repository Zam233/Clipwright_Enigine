"""M3 收尾：runtime 管理 API；M4：quality 对插件 kind 的视觉轨判定。"""
from __future__ import annotations

import sys

import pytest

from clipwright.agents.quality_agent import _is_visual_kind
from clipwright.api.plugin import RuntimeRegistration, list_runtimes, register_runtime_api
from clipwright.services.clip_kind_registry import ClipKindDefinition, ClipKindRegistry


@pytest.fixture(autouse=True)
def clean():
    ClipKindRegistry.clear()
    yield
    ClipKindRegistry.clear()


@pytest.mark.asyncio
async def test_register_and_list_runtime(monkeypatch):
    from clipwright.api import plugin as plugin_api

    monkeypatch.setattr(plugin_api, "_require_admin", lambda request: None)
    result = await register_runtime_api(
        RuntimeRegistration(
            runtime_id="probe_py", executable=sys.executable,
            version="1", approved=True,
        ),
        request=None,
    )
    assert result["approved"] is True

    listing = await list_runtimes(request=None)
    ids = {r["runtime_id"] for r in listing["runtimes"]}
    assert "probe_py" in ids
    # 列表不暴露可执行文件路径
    assert all("executable" not in r for r in listing["runtimes"])


def test_quality_treats_plugin_kind_as_visual():
    assert _is_visual_kind("video") is True
    assert _is_visual_kind("image") is True
    assert _is_visual_kind("audio") is False
    assert _is_visual_kind("plugin_scene") is False

    ClipKindRegistry.register(ClipKindDefinition(
        kind_id="plugin_scene", owner_plugin="demo",
        render_contract="media_overlay.v1",
    ))
    assert _is_visual_kind("plugin_scene") is True
