"""B6 测试：插件化缺陷修复（B1 异常不再吞、B2 协议符合性、B4 稳定镜像 id、
B5/B6 贡献读取缓存与镜像重扫）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.contribution import (
    BUILTIN_OWNER,
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    ContributionRegistry.clear_all()
    yield
    ContributionRegistry.clear_all()


class TestB1ContributionsFailure:
    """B1：contributions() 抛异常必须可见（错误通道 + 抛出），不能静默零贡献。"""

    def _plugin(self, exc: Exception | None, items=None):
        class Manifest:
            id = "plug_b1"
            contributions = []
            api_version = "1"  # 未声明白名单：注册不受「声明即授权」限制
            override_builtin = False

        class P:
            manifest = Manifest()

            def contributions(self):
                if exc is not None:
                    raise exc
                return items or []

        return P()

    def test_exception_is_raised_and_recorded(self):
        from clipwright.plugins.base import run_contributions
        from clipwright.plugins.error_bus import get_error_bus

        with pytest.raises(RuntimeError, match="contributions\\(\\) 执行失败"):
            run_contributions(self._plugin(ValueError("boom")))
        errs = get_error_bus().list(limit=50)
        assert any("contributions() 异常" in str(e.get("message", "")) for e in errs)

    def test_empty_contributions_still_returns_false(self):
        from clipwright.plugins.base import run_contributions

        assert run_contributions(self._plugin(None, [])) is False

    def test_non_empty_contributions_registers(self):
        from clipwright.plugins.base import run_contributions

        # 用 facade-only 的 kind（ui_slot）——不依赖后端注册表能否接受该对象
        item = Contribution(
            ContributionKind.UI_SLOT, "b1_slot", None,
            meta={"slot": "settings.section", "title": "B1"},
        )
        assert run_contributions(self._plugin(None, [item])) is True
        assert ContributionRegistry.owner_of(ContributionKind.UI_SLOT, "b1_slot") == "plug_b1"


class TestB2ProviderConformance:
    """B2：LLM provider 注册时校验协议方法，缺失即拒绝并给可读原因。"""

    def test_incomplete_provider_rejected_with_reason(self):
        from clipwright.plugins import contribution_dispatch

        class OnlyBuildClient:
            def build_client(self, **kwargs):  # pragma: no cover
                return None

        item = Contribution(ContributionKind.LLM_PROVIDER, "bad_llm", OnlyBuildClient())
        reason = contribution_dispatch.register("plug_b2", item)
        assert reason is not None
        assert "不符合协议" in reason
        assert "capabilities" in reason

    def test_complete_provider_accepted(self):
        from clipwright.providers.llm import OpenAIProvider
        from clipwright.plugins import contribution_dispatch

        item = Contribution(ContributionKind.LLM_PROVIDER, "good_llm", OpenAIProvider())
        assert contribution_dispatch.register("plug_b2", item) is None

    def test_runtime_accessors_do_not_raise(self):
        from clipwright.providers.base import (
            llm_capabilities, llm_normalize_messages, llm_uses_system_kwarg,
            missing_llm_provider_methods,
        )

        class Bare:
            name = "bare"

        assert missing_llm_provider_methods(Bare()) == [
            "build_client", "capabilities", "normalize_messages",
            "uses_system_kwarg", "default_model",
        ]
        caps = llm_capabilities(Bare(), "m")
        assert caps.tool_calls is False and caps.streaming is True  # 最小能力 + 告警
        msgs = llm_normalize_messages(Bare(), "u", "s")
        assert msgs[0]["role"] == "system"
        assert llm_uses_system_kwarg(Bare()) is False

    def test_broken_capabilities_falls_back(self):
        from clipwright.providers.base import llm_capabilities

        class Boom:
            name = "boom"
            def capabilities(self, model):
                raise RuntimeError("nope")

        assert llm_capabilities(Boom(), "m").tool_calls is False

    def test_wrong_capabilities_type_falls_back(self):
        from clipwright.providers.base import llm_capabilities

        class Weird:
            name = "weird"
            def capabilities(self, model):
                return {"tool_calls": True}

        assert llm_capabilities(Weird(), "m").tool_calls is False


class TestB4StableMirrorIds:
    """B4：hook/prompt 的镜像 id 不再用位置索引。"""

    def test_hook_ids_are_stable_across_order(self):
        from clipwright.plugins import builtin_contributions as bc

        def hook_a(**kwargs):  # pragma: no cover
            return None

        def hook_b(**kwargs):  # pragma: no cover
            return None

        first = bc._stable_id("builtin_pre_render", hook_a, "hook_0")
        second = bc._stable_id("builtin_pre_render", hook_b, "hook_1")
        # 位置（fallback 索引）变化不改变 id —— 这正是 B4 的幽灵项根因
        assert first == bc._stable_id("builtin_pre_render", hook_a, "hook_9")
        assert first != second
        assert "hook_a" in first  # 采用限定名而非位置

    def test_prompt_ids_include_content(self):
        from clipwright.plugins import builtin_contributions as bc

        a = bc._stable_id("builtin_edit", "你是剪辑师", "prompt_0")
        b = bc._stable_id("builtin_edit", "你是质检员", "prompt_1")
        assert a != b
        # 内容相同则 id 相同（与注册顺序无关）
        assert a == bc._stable_id("builtin_edit", "你是剪辑师", "prompt_5")


class TestB5B6ContributionCaching:
    """B5/B6：贡献读路径有缓存，且内置镜像可重扫 + 清理幽灵项。"""

    def test_ui_cache_and_scan_cache_invalidate(self):
        from clipwright.api import plugin as plugin_api

        plugin_api._scan_cache["at"] = 12345.0
        plugin_api._scan_cache["ids"] = ["p1"]
        plugin_api._ui_cache["p1"] = (1.0, {"title": "x", "widgets": []})
        plugin_api._invalidate_plugin_caches()
        assert plugin_api._scan_cache["at"] == 0.0
        assert plugin_api._ui_cache == {}

    def test_scan_cache_hits_within_ttl(self, monkeypatch):
        from clipwright.api import plugin as plugin_api

        class FakeLoader:
            plugin_dir = None
            calls = 0

            def discover(self):
                FakeLoader.calls += 1
                return ["a", "b"]

        monkeypatch.setattr(plugin_api, "_loader", FakeLoader())
        plugin_api._invalidate_plugin_caches()
        assert plugin_api._discover_plugins_cached() == ["a", "b"]
        assert plugin_api._discover_plugins_cached() == ["a", "b"]
        assert FakeLoader.calls == 1  # 第二次命中缓存，不再扫盘
        plugin_api._invalidate_plugin_caches()
        plugin_api._discover_plugins_cached()
        assert FakeLoader.calls == 2
        monkeypatch.setattr(plugin_api, "_loader", None)

    def test_mirror_refresh_ttl(self, monkeypatch):
        from clipwright.api import plugin as plugin_api

        calls: list[int] = []
        monkeypatch.setattr(
            "clipwright.plugins.builtin_contributions.ingest_builtin_contributions",
            lambda: calls.append(1),
        )
        plugin_api._mirror_at["at"] = 0.0
        plugin_api._maybe_refresh_builtin_mirror()
        plugin_api._maybe_refresh_builtin_mirror()  # TTL 内不再重扫
        assert len(calls) == 1
        plugin_api._mirror_at["at"] = 0.0
        plugin_api._maybe_refresh_builtin_mirror()
        assert len(calls) == 2

    def test_stale_builtin_mirror_swept(self):
        from clipwright.plugins.builtin_contributions import _sweep_stale

        ContributionRegistry.register(BUILTIN_OWNER, [
            Contribution(ContributionKind.HOOK, "ghost", object(), meta={"point": "pre_render"}),
            Contribution(ContributionKind.HOOK, "live", object(), meta={"point": "pre_render"}),
        ], dispatch=False)
        removed = _sweep_stale(ContributionKind.HOOK, {"live"})
        assert removed == ["ghost"]
        assert ContributionRegistry.resolve(ContributionKind.HOOK, "ghost") is None
        assert ContributionRegistry.resolve(ContributionKind.HOOK, "live") is not None

    def test_unregister_keys_only_matching_owner(self):
        ContributionRegistry.register("owner_a", [
            Contribution(ContributionKind.TOOL, "t1", object()),
        ], dispatch=False)
        ContributionRegistry.register("owner_b", [
            Contribution(ContributionKind.TOOL, "t2", object()),
        ], dispatch=False)
        out = ContributionRegistry.unregister_keys(
            "owner_a", [(ContributionKind.TOOL, "t1"), (ContributionKind.TOOL, "t2")],
        )
        assert out == ["tool:t1"]
        assert ContributionRegistry.resolve(ContributionKind.TOOL, "t2") is not None
