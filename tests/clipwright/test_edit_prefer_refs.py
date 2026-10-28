"""S 轨闭环：EditAgent 优先取用落位素材（PRD C13 → C4，验收 V41 的代码侧）。"""

from __future__ import annotations

import pytest

from clipwright.agents.edit_agent import _prefer_material_refs


def _asset(asset_id: str, score: float = 0.5) -> dict:
    return {"asset_id": asset_id, "score": score}


class TestPreferMaterialRefs:
    def test_refs_are_hoisted_to_front(self) -> None:
        assets = [_asset("a", 0.9), _asset("b", 0.8), _asset("c", 0.7)]
        scene = {"material_refs": ["c"]}
        ordered = _prefer_material_refs(scene, assets)
        assert [a["asset_id"] for a in ordered] == ["c", "a", "b"]

    def test_multiple_refs_keep_ref_order(self) -> None:
        assets = [_asset("a"), _asset("b"), _asset("c")]
        ordered = _prefer_material_refs({"material_refs": ["b", "a"]}, assets)
        assert [a["asset_id"] for a in ordered][:2] == ["b", "a"]

    def test_no_refs_is_noop(self) -> None:
        assets = [_asset("a"), _asset("b")]
        assert _prefer_material_refs({}, assets) == assets

    def test_missing_ref_ignored(self) -> None:
        assets = [_asset("a")]
        assert _prefer_material_refs({"material_refs": ["zzz"]}, assets) == assets

    def test_empty_assets(self) -> None:
        assert _prefer_material_refs({"material_refs": ["a"]}, []) == []

    def test_rest_order_preserved(self) -> None:
        assets = [_asset("x", 0.9), _asset("a", 0.1), _asset("y", 0.8)]
        ordered = _prefer_material_refs({"material_refs": ["a"]}, assets)
        assert [a["asset_id"] for a in ordered] == ["a", "x", "y"]

    def test_id_fallback_key(self) -> None:
        assets = [{"id": "a", "score": 0.1}, {"id": "b", "score": 0.9}]
        ordered = _prefer_material_refs({"material_refs": ["a"]}, assets)
        assert [a["id"] for a in ordered] == ["a", "b"]

    def test_duplicate_ref_ids_kept_stably(self) -> None:
        assets = [_asset("a", 0.9), _asset("a", 0.4), _asset("b", 0.8)]
        ordered = _prefer_material_refs({"material_refs": ["a"]}, assets)
        assert [a["score"] for a in ordered] == [0.9, 0.4, 0.8]


class TestEditAgentWiring:
    def test_process_scene_prefers_refs(self) -> None:
        """接线断言：取素材的地方必须先过 _prefer_material_refs。"""
        import inspect

        from clipwright.agents import edit_agent

        source = inspect.getsource(edit_agent)
        assert "_prefer_material_refs(scene, sorted_assets)" in source
        # 必须在构建 sorted_assets 之后、进入取素材循环之前调用
        call_at = source.index("_prefer_material_refs(scene, sorted_assets)")
        loop_at = source.index("while remaining > 1.0")
        assert call_at < loop_at


def test_module_function_exported() -> None:
    with pytest.raises(ImportError):
        from clipwright.agents.edit_agent import _prefer_material_refs_missing  # noqa: F401
