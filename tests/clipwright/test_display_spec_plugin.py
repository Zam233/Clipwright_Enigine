"""`display_spec`（第 18 类纯数据扩展点）的形态契约测试 —— B1 交付面。

覆盖（PRD §3.1 N1–N5 + coding plan B1-T1.5 的 10 条）：
1. 合法注册后 `list_contracts` 可见、字段齐全；
2. `object` 非 None → 拒；
3. `meta` 不可 JSON 序列化 → 拒；
4. 缺 `limits` → 拒（原因含 limits）；
5. `trust` 非法值 → 拒；
6. `spec_id != id` → 拒；
7. 同 id 二次注册（不同 owner）→ REJECT；
8. 显式 OVERRIDE 也不能盖掉别人的 display_spec → 拒；
9. 纯数据贡献走 dispatch 直通（object=None 即可，不需要代理对象）；
10. `DISPLAY_SPEC` 不进 `_WIRED_KINDS`（没有后端注册表），且 `trust=recipe_only` 必须有 recipe。
"""
from __future__ import annotations

import copy

import pytest

from clipwright.plugins.contribution import (
    ConflictPolicy,
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.plugins import contribution_dispatch as dispatch
from clipwright.services.display_spec import get_contract, list_contracts, normalize_contract

SPEC_ID = "brief"


def _meta(**overrides):
    meta = {
        "spec_id": SPEC_ID,
        "version": 1,
        "source": "brief",
        "components": ["text", "table", "row"],
        "containers": ["row"],
        "actions": ["ask"],
        "tones": ["info"],
        "badge_tones": ["accent"],
        "text_sizes": ["body"],
        "limits": {
            "max_nodes": 100, "max_depth": 6, "max_text_chars": 4000,
            "max_spec_bytes": 65536, "max_array_items": 100, "max_table_cols": 12,
            "max_action_payload_chars": 2000, "max_select_options": 6, "max_option_chars": 40,
        },
        "order": 100,
        "trust": "sanitize",
    }
    meta.update(overrides)
    return meta


@pytest.fixture(autouse=True)
def _clean():
    yield
    ContributionRegistry.unregister_plugin("plug_ds")
    ContributionRegistry.unregister_plugin("plug_other")


class TestContractRead:
    def test_valid_registration_is_visible(self):
        owner = "plug_ds"
        ContributionRegistry.declared_scope(owner, [f"display_spec:{SPEC_ID}"])
        report = ContributionRegistry.register(
            owner, [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())]
        )
        assert report.ok, report.rejected
        contracts = list_contracts()
        assert [c.spec_id for c in contracts] == [SPEC_ID]
        contract = get_contract(SPEC_ID)
        assert contract is not None
        assert contract.sanitize_model_spec is True
        assert contract.components == ("text", "table", "row")
        assert contract.limit("max_nodes") == 100
        assert contract.owner == owner

    def test_unregister_hides_contract(self):
        owner = "plug_ds"
        ContributionRegistry.declared_scope(owner, [f"display_spec:{SPEC_ID}"])
        ContributionRegistry.register(
            owner, [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())]
        )
        ContributionRegistry.unregister_plugin(owner)
        assert get_contract(SPEC_ID) is None
        assert list_contracts() == []


class TestDataOnlyShape:
    def test_object_must_be_none(self):
        reason = dispatch.register(
            "plug_ds", Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, object(), _meta())
        )
        assert reason and "object 必须为 None" in reason

    def test_meta_must_be_json_serializable(self):
        meta = _meta(extra=object())
        reason = dispatch.register(
            "plug_ds", Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, meta)
        )
        assert reason and "JSON 序列化" in reason

    def test_missing_limits_rejected(self):
        meta = _meta()
        meta.pop("limits")
        reason = dispatch.register(
            "plug_ds", Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, meta)
        )
        assert reason and "limits" in reason

    def test_bad_trust_rejected(self):
        reason = dispatch.register(
            "plug_ds",
            Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta(trust="trust_me")),
        )
        assert reason and "trust" in reason

    def test_spec_id_must_equal_id(self):
        reason = dispatch.register(
            "plug_ds",
            Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta(spec_id="plan")),
        )
        assert reason and "spec_id" in reason

    def test_recipe_only_requires_recipe(self):
        reason = dispatch.register(
            "plug_ds",
            Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta(trust="recipe_only")),
        )
        assert reason and "recipe" in reason

    def test_recipe_only_with_recipe_accepted(self):
        meta = _meta(trust="recipe_only", recipe={"title": "T", "nodes": [{"op": "text"}]})
        reason = dispatch.register(
            "plug_ds", Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, meta)
        )
        assert reason is None

    def test_data_only_path_is_used(self):
        """object=None 时不经任何代理/协议校验就直通（子进程档同一条路）。"""
        reason = dispatch.register(
            "plug_ds", Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())
        )
        assert reason is None


class TestRegistrationConflict:
    def test_second_owner_rejected(self):
        ContributionRegistry.declared_scope("plug_ds", [f"display_spec:{SPEC_ID}"])
        ContributionRegistry.register(
            "plug_ds", [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())]
        )
        ContributionRegistry.declared_scope("plug_other", [f"display_spec:{SPEC_ID}"])
        report = ContributionRegistry.register(
            "plug_other", [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())]
        )
        assert not report.ok
        assert any("已被" in reason or "不得覆盖" in reason for _, reason in report.rejected)

    def test_override_policy_rejected(self):
        ContributionRegistry.declared_scope("plug_ds", [f"display_spec:{SPEC_ID}"])
        ContributionRegistry.register(
            "plug_ds", [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())]
        )
        ContributionRegistry.declared_scope("plug_other", [f"display_spec:{SPEC_ID}"])
        report = ContributionRegistry.register(
            "plug_other",
            [Contribution(ContributionKind.DISPLAY_SPEC, SPEC_ID, None, _meta())],
            policy=ConflictPolicy.OVERRIDE,
        )
        assert not report.ok
        assert any("display_spec" in reason and "override" in reason.lower()
                   for _, reason in report.rejected) or any(
            "已被" in reason for _, reason in report.rejected
        )


class TestWiringInvariants:
    def test_not_in_wired_kinds(self):
        assert ContributionKind.DISPLAY_SPEC not in dispatch._WIRED_KINDS
        assert ContributionKind.DISPLAY_SPEC in dispatch._DATA_ONLY_KINDS

    def test_normalize_reports_problems_for_bad_meta(self):
        contract, problems = normalize_contract({"spec_id": "x"})
        assert contract is None
        assert problems

    def test_normalize_is_pure(self):
        meta = _meta()
        snapshot = copy.deepcopy(meta)
        contract, problems = normalize_contract(meta)
        assert contract is not None and problems == []
        assert meta == snapshot, "归一不得修改入参"
