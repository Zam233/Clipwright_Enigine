"""展示层接线的行为契约（B3：指纹排除 / 装配 / 字段形状 / 读路径归一）。

本文件对应编码计划 B3 的 T3.1（指纹）、T3.4（装配）、T3.5（字段）、T3.6（读路径与接线）。
"""
from __future__ import annotations

import copy

from clipwright.services.requirements_service import _brief_hash


class TestBriefHash:
    """T3.1：**整个展示容器**不得进简报指纹（C10）。

    老键 `genui`（2026-11-03~07 落库）与新容器 `display_specs` 都要排除；只改业务字段才换指纹。
    """

    def test_genui_is_excluded(self):
        assert _brief_hash({"title": "T"}) == _brief_hash(
            {"title": "T", "genui": {"version": 1, "items": [{"type": "text", "content": "x"}]}}
        )

    def test_display_specs_is_excluded(self):
        assert _brief_hash({"title": "T"}) == _brief_hash(
            {"title": "T", "display_specs": {"brief": {"version": 1, "items": [{"type": "text"}]}}}
        )

    def test_both_keys_injected_still_excluded(self):
        payload = {
            "title": "T",
            "genui": {"version": 1, "items": [{"type": "text", "content": "old"}]},
            "display_specs": {"brief": {"version": 1, "items": [{"type": "text", "content": "new"}]}},
        }
        assert _brief_hash({"title": "T"}) == _brief_hash(payload)
        assert _brief_hash(payload) != _brief_hash(payload | {"title": "T2"})

    def test_real_field_change_still_changes_hash(self):
        assert _brief_hash({"title": "T"}) != _brief_hash({"title": "T2"})

    def test_spec_value_change_does_not_change_hash(self):
        base = {"title": "T", "display_specs": {"brief": {"items": [{"type": "text", "content": "a"}]}}}
        other = {"title": "T", "display_specs": {"brief": {"items": [{"type": "text", "content": "b"}]}}}
        assert _brief_hash(base) == _brief_hash(other)

    def test_empty_inputs(self):
        assert _brief_hash({}) == ""
        assert _brief_hash(None) == ""

    def test_display_only_payload_has_no_hash(self):
        assert _brief_hash({"genui": {"items": [{"type": "text", "content": "x"}]}}) == ""
        assert _brief_hash({"display_specs": {"brief": {"items": []}}}) == ""
