"""B6：代理的 contribution_id 归属校验（跨仓契约）。

2026-11-13 实测：前端把 `owner:id` 合成键当贡献 id 发送，后端只按裸 id 查门面，
导致**所有布局按钮 403**。此文件固定「裸 id / owner:id / legacy 槽」三种形状。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from clipwright.api import plugin as pj
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


@pytest.fixture(autouse=True)
def _registered_slot():
    ContributionRegistry.clear_all()
    ContributionRegistry.declared_scope("demo_plug", ["ui_slot:panel_a"])
    ContributionRegistry.register(
        "demo_plug",
        [Contribution(ContributionKind.UI_SLOT, "panel_a", {"slot": "editor.right-panel"})],
    )
    yield
    ContributionRegistry.unregister_plugin("demo_plug")


def test_bare_id_owned_by_plugin_is_accepted():
    assert pj._contribution_owned_by("demo_plug", "panel_a") is True


def test_owner_id_form_is_accepted():
    """旧版前端把 stateKey(`owner:id`) 当 id 发，服务端应兼容。"""
    assert pj._contribution_owned_by("demo_plug", "demo_plug:panel_a") is True


def test_other_owner_prefix_is_rejected():
    assert pj._contribution_owned_by("demo_plug", "other_plug:panel_a") is False


def test_other_plugins_slot_id_is_rejected():
    assert pj._contribution_owned_by("demo_plug", "panel_of_someone_else") is False


def test_legacy_ui_json_slot_is_accepted(monkeypatch):
    """旧 ui.json 槽由 /contributions 现造，不进门面，按 `<plugin>_legacy` 命名归属。

    `legacy` 是旧前端 `stateKey=plugin:legacy` 被剥前缀后的形状；两种都只在该插件
    确实有 ui.json 时放行。
    """
    monkeypatch.setattr(pj, "_loader", _loader_stub())
    assert pj._contribution_owned_by("ai_image_gen", "ai_image_gen_legacy") is True
    assert pj._contribution_owned_by("ai_image_gen", "ai_image_gen:legacy") is True
    assert pj._contribution_owned_by("ai_image_gen", "ai_music_gen_legacy") is False


def test_empty_contribution_id_skips_check():
    """旧客户端不上报贡献 id，行为保持不变（不能因此拒绝对话）。"""
    assert pj._contribution_owned_by("demo_plug", "") is True


async def test_proxy_rejects_mismatched_contribution_before_allowlist(monkeypatch):
    class _Loader:
        @staticmethod
        def discover():
            return ["ai_image_gen"]

        @staticmethod
        def is_enabled(pid):
            return True

        @staticmethod
        def get(pid):
            return object()

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy(
            "ai_image_gen", "post",
            pj._PluginProxyBody(
                action="tool", payload={}, contribution_id="other_plug:panel",
            ),
            None,
        )
    assert ei.value.status_code == 403
    assert "归属" in ei.value.detail


async def test_proxy_accepts_owner_prefixed_contribution_id(monkeypatch):
    """`owner:id` 形状通过归属校验后，应继续走白名单/工具校验而不是 403 归属。"""
    class _Loader:
        @staticmethod
        def discover():
            return ["demo_plug"]

        @staticmethod
        def is_enabled(pid):
            return True

        @staticmethod
        def get(pid):
            return object()

    monkeypatch.setattr(pj, "_loader", _Loader())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy(
            "demo_plug", "post",
            pj._PluginProxyBody(
                action="tool", payload={},
                contribution_id="demo_plug:panel_a",
            ),
            None,
        )
    # demo_plug 不在服务端白名单 → 403 未授权动作（而不是归属不匹配）
    assert ei.value.status_code == 403
    assert "未被授权" in ei.value.detail


@pytest.fixture
def owned_tool(monkeypatch):
    """在门面与 ToolRegistry 里造一个属于 ai_image_gen 的合法工具。"""
    from clipwright.schema.tool import ToolExecResult
    from clipwright.tool.base import BaseTool
    from clipwright.tool.registry import ToolRegistry

    class _ProbeTool(BaseTool):
        name = "ai_image_gen_probe"
        description = "probe"
        capability = ""
        permission_tier = "costly"

        async def execute(self, **kwargs):
            return ToolExecResult(status="success", output={"echo": kwargs})

        def to_llm_tool(self):
            return {"name": self.name, "description": self.description, "parameters": {}}

    tool = _ProbeTool()
    ToolRegistry.register(tool)
    tool._plugin_id = "ai_image_gen"
    ContributionRegistry.declared_scope("ai_image_gen", ["tool:ai_image_gen_probe"])
    ContributionRegistry.register(
        "ai_image_gen",
        [Contribution(ContributionKind.TOOL, "ai_image_gen_probe", tool)],
    )
    yield tool
    ToolRegistry.unregister("ai_image_gen_probe")
    ContributionRegistry.unregister_plugin("ai_image_gen")


def _loader_stub():
    class _Loader:
        @staticmethod
        def discover():
            return ["ai_image_gen"]

        @staticmethod
        def is_enabled(pid):
            return True

        @staticmethod
        def get(pid):
            return object()

        @property
        def plugin_dir(self):
            from pathlib import Path

            return Path(__file__).resolve().parents[2] / "plugins"

    return _Loader()


@pytest.mark.parametrize("contribution_id", [
    "",                       # 旧客户端：不报 id
    "ai_image_gen_legacy",    # legacy 槽的规范 id
    "ai_image_gen:legacy",    # 旧前端把 stateKey 当 id 发
])
async def test_legacy_panel_tool_call_succeeds(monkeypatch, owned_tool, contribution_id):
    """B6 正例：ai_image_gen 的 legacy 面板按钮必须真的能调到自己的工具。"""
    monkeypatch.setattr(pj, "_loader", _loader_stub())
    result = await pj.plugin_proxy(
        "ai_image_gen", "post",
        pj._PluginProxyBody(
            action="tool",
            payload={"name": "ai_image_gen_probe", "params": {"prompt": "猫"}},
            contribution_id=contribution_id,
        ),
        None,
    )
    assert result["status"] == "success"
    assert result["output"]["echo"] == {"prompt": "猫"}


async def test_tool_call_rejected_when_contribution_belongs_to_other_plugin(
    monkeypatch, owned_tool,
):
    monkeypatch.setattr(pj, "_loader", _loader_stub())
    with pytest.raises(HTTPException) as ei:
        await pj.plugin_proxy(
            "ai_image_gen", "post",
            pj._PluginProxyBody(
                action="tool",
                payload={"name": "ai_image_gen_probe", "params": {}},
                contribution_id="other_plug:panel",
            ),
            None,
        )
    assert ei.value.status_code == 403
    assert "归属" in ei.value.detail
