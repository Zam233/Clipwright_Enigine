"""M1 契约冻结测试 —— Contribution 契约（方案 A-0）。

覆盖：register / resolve / unregister / list / owner_of / declared_scope
与四类冲突场景（内置 vs 插件、插件 vs 插件、同 owner 重复、未声明 id）。
"""
from __future__ import annotations

import pytest

from clipwright.plugins.contribution import (
    BUILTIN_OWNER,
    ConflictPolicy,
    Contribution,
    ContributionKind,
    ContributionRegistry,
    RegistrationReport,
)


@pytest.fixture(autouse=True)
def _isolate_dispatch(monkeypatch):
    """M1 契约测试只验证门面语义——dispatch 转调由 test_plugin_dispatch 覆盖，
    此处隔离（否则假对象会被真实转调进各注册表并失败）。"""
    import clipwright.plugins.contribution as _c
    monkeypatch.setattr(_c, "_dispatch_register", lambda owner, item: None)
    monkeypatch.setattr(_c, "_dispatch_unregister", lambda owner, keys: list(keys))


@pytest.fixture(autouse=True)
def _clean_registry():
    ContributionRegistry.clear_all()
    yield
    ContributionRegistry.clear_all()


def _c(kind: ContributionKind, cid: str, obj: object = None, **meta) -> Contribution:
    return Contribution(kind=kind, id=cid, object=obj if obj is not None else f"obj:{cid}", meta=meta)


class TestBasicLifecycle:
    def test_register_resolve_list_owner(self):
        item = _c(ContributionKind.TOOL, "my_tool")
        report = ContributionRegistry.register("plug_a", [item])
        assert isinstance(report, RegistrationReport)
        assert report.ok and report.accepted == [item]

        resolved = ContributionRegistry.resolve(ContributionKind.TOOL, "my_tool")
        assert resolved is item
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, "my_tool") == "plug_a"
        assert ContributionRegistry.list(ContributionKind.TOOL) == [("plug_a", item)]

    def test_unregister_plugin_recycles_all_kinds(self):
        items = [
            _c(ContributionKind.TOOL, "t1"),
            _c(ContributionKind.SKILL, "s1"),
            _c(ContributionKind.UI_SLOT, "u1", slot="editor.right-panel"),
        ]
        ContributionRegistry.register("plug_b", items)
        assert len(ContributionRegistry.list()) == 3

        recycled = ContributionRegistry.unregister_plugin("plug_b")
        assert sorted(recycled) == ["skill:s1", "tool:t1", "ui_slot:u1"]
        assert ContributionRegistry.list() == []
        assert ContributionRegistry.resolve(ContributionKind.TOOL, "t1") is None
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, "t1") == ""

    def test_unregister_unknown_owner_is_noop(self):
        assert ContributionRegistry.unregister_plugin("never_loaded") == []

    def test_builtin_registration_does_not_require_declaration(self):
        """内置贡献不经 manifest——声明白名单不适用于 BUILTIN_OWNER。"""
        report = ContributionRegistry.register(BUILTIN_OWNER, [_c(ContributionKind.TOOL, "builtin_t")])
        assert report.ok
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, "builtin_t") == BUILTIN_OWNER


class TestConflictScenarios:
    def test_plugin_cannot_override_builtin(self):
        ContributionRegistry.register(BUILTIN_OWNER, [_c(ContributionKind.TOOL, "video_trim")])
        report = ContributionRegistry.register("plug_x", [_c(ContributionKind.TOOL, "video_trim")])
        assert not report.ok
        assert "内置" in report.rejected[0][1]
        # 内置仍可用
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, "video_trim") == BUILTIN_OWNER

    def test_override_policy_allows_builtin_override(self):
        ContributionRegistry.register(BUILTIN_OWNER, [_c(ContributionKind.TOOL, "video_trim")])
        replacement = _c(ContributionKind.TOOL, "video_trim", obj="new")
        report = ContributionRegistry.register(
            "plug_x", [replacement], policy=ConflictPolicy.OVERRIDE,
        )
        assert report.ok
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, "video_trim") == "plug_x"

    def test_plugin_vs_plugin_rejected(self):
        ContributionRegistry.register("plug_a", [_c(ContributionKind.SKILL, "sk")])
        report = ContributionRegistry.register("plug_b", [_c(ContributionKind.SKILL, "sk")])
        assert not report.ok
        assert "plug_a" in report.rejected[0][1]

    def test_same_owner_reregister_is_idempotent(self):
        """reload/热重载路径：同 owner 重复注册允许（幂等覆盖）。"""
        ContributionRegistry.register("plug_a", [_c(ContributionKind.TOOL, "t1", obj="v1")])
        report = ContributionRegistry.register("plug_a", [_c(ContributionKind.TOOL, "t1", obj="v2")])
        assert report.ok
        resolved = ContributionRegistry.resolve(ContributionKind.TOOL, "t1")
        assert resolved is not None and resolved.object == "v2"

    def test_undeclared_id_rejected(self):
        """声明即授权：白名单外注册被拒，白名单内放行。"""
        ContributionRegistry.declared_scope("plug_a", ["tool:allowed"])
        report = ContributionRegistry.register("plug_a", [
            _c(ContributionKind.TOOL, "allowed"),
            _c(ContributionKind.TOOL, "sneaky"),
        ])
        assert len(report.accepted) == 1 and report.accepted[0].id == "allowed"
        assert len(report.rejected) == 1
        assert "未在 manifest 声明" in report.rejected[0][1]
        assert ContributionRegistry.resolve(ContributionKind.TOOL, "sneaky") is None

    def test_no_declared_scope_means_no_check(self):
        """未调 declared_scope 的 owner 不受白名单限制（灰度期旧插件）。"""
        report = ContributionRegistry.register("legacy_plug", [_c(ContributionKind.TOOL, "any")])
        assert report.ok

    def test_empty_id_rejected(self):
        report = ContributionRegistry.register("plug_a", [_c(ContributionKind.TOOL, "")])
        assert not report.ok


class TestDeclaredScope:
    def test_clear_declared_on_unregister(self):
        ContributionRegistry.declared_scope("plug_a", ["tool:t1"])
        ContributionRegistry.register("plug_a", [_c(ContributionKind.TOOL, "t1")])
        ContributionRegistry.unregister_plugin("plug_a")
        assert ContributionRegistry.declared_keys("plug_a") == set()
        # 卸载后同 owner 无声明 → 不再校验（重新加载时会重新声明）
        assert ContributionRegistry.register("plug_a", [_c(ContributionKind.TOOL, "t2")]).ok

    def test_meta_preserved(self):
        item = _c(ContributionKind.HOOK, "h1", point="pre_agent")
        ContributionRegistry.register("plug_a", [item])
        resolved = ContributionRegistry.resolve(ContributionKind.HOOK, "h1")
        assert resolved is not None and resolved.meta == {"point": "pre_agent"}
        assert resolved.key == "hook:h1"
