"""D3 测试：StyleInterpreter 多注册（共存 / 优先级 / 按 owner 回收）。

缺陷原状：单槽 + 不同 owner 的第二个注册抛 ValueError → 后安装的插件贡献被拒，
用户「装了却没生效」。现在每个 owner 一个槽位，运行时按 priority 选活跃项。
"""
from __future__ import annotations

import pytest

from clipwright.services.style_interpreter import StyleInterpreter


class _Manifest:
    def __init__(self, pid: str) -> None:
        self.id = pid


class _FakeInterp:
    """最小 StyleInterpreterPlugin 替身（只需 manifest + interpret）。"""

    def __init__(self, pid: str, result: dict | None = None) -> None:
        self.manifest = _Manifest(pid)
        self.calls = 0
        self._result = result if result is not None else {"primary_color": "#111111"}

    async def interpret(self, visual_config, persona_context):
        self.calls += 1
        return dict(self._result)


@pytest.fixture(autouse=True)
def _clean():
    StyleInterpreter.reset_for_tests()
    yield
    StyleInterpreter.reset_for_tests()


class TestMultiRegistration:
    def test_second_owner_no_longer_rejected(self):
        a = _FakeInterp("plug_a")
        b = _FakeInterp("plug_b")
        StyleInterpreter.register_plugin(a, owner="plug_a")
        StyleInterpreter.register_plugin(b, owner="plug_b")  # 缺陷原状会抛 ValueError
        owners = [o for o, _p, _pl in StyleInterpreter.list_plugins()]
        assert owners == ["plug_a", "plug_b"]  # 两个都留住了

    def test_same_owner_replaces(self):
        first = _FakeInterp("same_1")
        second = _FakeInterp("same_2")
        StyleInterpreter.register_plugin(first, owner="p")
        StyleInterpreter.register_plugin(second, owner="p")
        assert [pl for _o, _p, pl in StyleInterpreter.list_plugins()] == [second]

    def test_higher_priority_wins_and_ties_keep_registration_order(self):
        low = _FakeInterp("low")
        high = _FakeInterp("high")
        StyleInterpreter.register_plugin(low, owner="a", priority=10)
        StyleInterpreter.register_plugin(high, owner="b", priority=200)
        assert StyleInterpreter._plugin is high  # 高优先级活跃
        assert StyleInterpreter._owner == "b"

        tie = _FakeInterp("tie")
        StyleInterpreter.register_plugin(tie, owner="c", priority=200)
        assert StyleInterpreter._plugin is high  # 同优先级：先注册者胜（确定）

    def test_unregister_only_removes_own_slot_and_reelects(self):
        a = _FakeInterp("a")
        b = _FakeInterp("b")
        StyleInterpreter.register_plugin(a, owner="a", priority=200)
        StyleInterpreter.register_plugin(b, owner="b", priority=10)
        assert StyleInterpreter.unregister_plugin("nonexistent") is False
        assert StyleInterpreter.unregister_plugin("b") is True  # 只能回收自己的槽位
        assert StyleInterpreter._plugin is a
        assert StyleInterpreter.unregister_plugin("a") is True
        assert StyleInterpreter._plugin is None and StyleInterpreter._owner == ""

    def test_unrelated_owner_cannot_evict(self):
        a = _FakeInterp("a")
        StyleInterpreter.register_plugin(a, owner="a")
        assert StyleInterpreter.unregister_plugin("other") is False
        assert StyleInterpreter._plugin is a  # 未被误回收


class TestInterpretDispatch:
    @pytest.mark.asyncio
    async def test_active_plugin_is_used(self):
        a = _FakeInterp("a", {"primary_color": "#aaaaaa"})
        b = _FakeInterp("b", {"primary_color": "#bbbbbb"})
        StyleInterpreter.register_plugin(a, owner="a", priority=10)
        StyleInterpreter.register_plugin(b, owner="b", priority=99)
        out = await StyleInterpreter.interpret({}, {})
        assert out["primary_color"] == "#bbbbbb"
        assert b.calls == 1 and a.calls == 0

    @pytest.mark.asyncio
    async def test_failing_active_falls_back_to_llm(self, monkeypatch):
        class Boom(_FakeInterp):
            async def interpret(self, visual_config, persona_context):
                raise RuntimeError("boom")

        StyleInterpreter.register_plugin(Boom("boom"), owner="boom")

        async def fake_llm(visual_config, persona_context):
            return {"primary_color": "#fallback"}

        monkeypatch.setattr(StyleInterpreter, "_llm_interpret", fake_llm)
        out = await StyleInterpreter.interpret({}, {})
        assert out["primary_color"] == "#fallback"


class TestPluginLoadCoexistence:
    """两个插件各带一个 style_interpreter 贡献：都必须加载成功（D3 核心诉求）。"""

    def _plugin(self, pid: str, interp: _FakeInterp):
        from clipwright.plugins.contribution import Contribution, ContributionKind
        from clipwright.schema.plugin import ContributionDecl, PluginManifest

        class Manifest(PluginManifest):
            pass

        manifest = Manifest(
            id=pid, name=pid, version="1.0.0", kind="capability", api_version="2",
            contributions=[ContributionDecl(kind="style_interpreter", id=f"{pid}_style")],
        )

        class P:
            def __init__(self) -> None:
                self.manifest = manifest

            def contributions(self):
                return [Contribution(
                    ContributionKind.STYLE_INTERPRETER, f"{pid}_style", interp,
                    meta={"priority": 100},
                )]

        return P()

    def test_both_plugins_register_without_conflict(self):
        from clipwright.plugins.base import run_contributions
        from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

        ContributionRegistry.clear_all()
        try:
            assert run_contributions(self._plugin("coexist_a", _FakeInterp("ia"))) is True
            assert run_contributions(self._plugin("coexist_b", _FakeInterp("ib"))) is True
            assert len(StyleInterpreter.list_plugins()) == 2
            # 两个插件各自的贡献都在门面账本里（第二个不再被拒）
            assert ContributionRegistry.resolve(
                ContributionKind.STYLE_INTERPRETER, "coexist_a_style") is not None
            assert ContributionRegistry.resolve(
                ContributionKind.STYLE_INTERPRETER, "coexist_b_style") is not None
        finally:
            StyleInterpreter.reset_for_tests()
            ContributionRegistry.clear_all()
