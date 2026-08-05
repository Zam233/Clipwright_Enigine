"""内置 MG 模板库测试 — 8 个专业模板 JSON 校验与降级命中。

覆盖：
- 8 个模板 JSON 全部通过 validate_mg_json
- list_templates() >= 8 且 id 完整
- 模板设计规范：元素 >= 4、每元素关键帧 >= 3、首个关键帧 time=0 隐藏、
  至少 2 个关键帧带 easing、错峰间隔 0.2-0.5s、时间不超 duration_sec
- FallbackEngine.find_best_template 关键词命中全部 8 个 id
- MGRenderer 可渲染每个模板（无异常）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright.animation.mg import list_templates
from clipwright.animation.mg.fallback import FallbackEngine
from clipwright.animation.mg.validator import validate_mg_json
from clipwright.animation.mg_renderer import MGRenderer

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "mg" / "templates"

EXPECTED_TEMPLATE_IDS = [
    "mg_title_reveal",
    "mg_comparison_split",
    "mg_data_bars",
    "mg_timeline_progress",
    "mg_counter_up",
    "mg_flow_arrows",
    "mg_quote_card",
    "mg_mindmap",
]


def _load_template(anim_id: str) -> dict:
    path = TEMPLATES_DIR / f"{anim_id}.json"
    assert path.exists(), f"template file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


class TestTemplateLibrary:
    """模板库存在性与元信息。"""

    def test_templates_dir_exists(self) -> None:
        assert TEMPLATES_DIR.is_dir()

    def test_list_templates_count(self) -> None:
        assert len(list_templates()) >= 8

    def test_list_templates_ids_complete(self) -> None:
        ids = {t["animation_id"] for t in list_templates()}
        for tid in EXPECTED_TEMPLATE_IDS:
            assert tid in ids, f"template id missing from list_templates: {tid}"

    def test_each_template_has_metadata(self) -> None:
        for tid in EXPECTED_TEMPLATE_IDS:
            data = _load_template(tid)
            assert data.get("animation_id") == tid
            assert data.get("name")
            assert data.get("description")
            assert isinstance(data.get("params", {}), dict)
            assert isinstance(data.get("style", {}), dict)

    @pytest.mark.parametrize("anim_id", EXPECTED_TEMPLATE_IDS)
    def test_template_passes_validation(self, anim_id: str) -> None:
        data = _load_template(anim_id)
        ok, errors = validate_mg_json(data)
        assert ok, f"{anim_id} failed validation: {errors}"

    @pytest.mark.parametrize("anim_id", EXPECTED_TEMPLATE_IDS)
    def test_template_design_principles(self, anim_id: str) -> None:
        """模板遵循 config.yaml 硬性约束（元素数/关键帧数/easing/首个隐藏/time 上限）。"""
        data = _load_template(anim_id)
        dur = data["duration_sec"]
        elems = data["elements"]
        assert len(elems) >= 4, f"{anim_id}: elements < 4"

        easing_count = 0
        for elem in elems:
            kfs = elem["keyframes"]
            assert len(kfs) >= 3, f"{anim_id} elem {elem['type']}: keyframes < 3"
            # 首个关键帧 time=0 且隐藏
            first = kfs[0]
            assert first["time"] == 0, f"{anim_id} elem {elem['type']}: first kf time != 0"
            assert first["opacity"] == 0, f"{anim_id} elem {elem['type']}: first kf not hidden"
            for kf in kfs:
                assert 0 <= kf["time"] <= dur, f"{anim_id}: kf time {kf['time']} out of range"
                if "easing" in kf:
                    easing_count += 1
        assert easing_count >= 2, f"{anim_id}: easing used on < 2 keyframes"

    @pytest.mark.parametrize("anim_id", EXPECTED_TEMPLATE_IDS)
    def test_template_uses_new_features(self, anim_id: str) -> None:
        """每个模板使用新特性：发光、渐变、错峰、粒子。"""
        data = _load_template(anim_id)
        elems = data["elements"]
        has_glow = any(e.get("box_shadow") or e.get("text_shadow") for e in elems)
        has_gradient = any("linear-gradient" in str(e.get("background", "")) for e in elems)
        assert has_glow, f"{anim_id}: missing glow (box_shadow/text_shadow)"
        assert has_gradient, f"{anim_id}: missing gradient background"
        # 错峰：存在至少两个元素的首个可见关键帧间隔在 0.2-0.5s
        entry_times = []
        for e in elems:
            kfs = e["keyframes"]
            entry = next((kf["time"] for kf in kfs if kf.get("opacity", 0) > 0), None)
            if entry is not None:
                entry_times.append(entry)
        sorted_times = sorted(set(entry_times))
        assert len(sorted_times) >= 3, f"{anim_id}: not enough distinct entry times for stagger"
        gaps = [b - a for a, b in zip(sorted_times, sorted_times[1:])]
        assert any(0.2 <= g <= 0.5 for g in gaps), f"{anim_id}: no stagger gap in 0.2-0.5s ({gaps})"
        # 粒子：小尺寸 circle 点缀（部分模板含 8+ 粒子氛围层）
        particle_count = sum(1 for e in elems if e["type"] == "circle")
        assert particle_count >= 4, f"{anim_id}: expected particle circles, got {particle_count}"

    @pytest.mark.parametrize("anim_id", EXPECTED_TEMPLATE_IDS)
    def test_template_renders(self, anim_id: str) -> None:
        """每个模板都能被 MGRenderer 渲染为 HTML。"""
        data = _load_template(anim_id)
        html = MGRenderer.render(data)
        assert "<!DOCTYPE html>" in html
        assert f"data-duration=\"{data['duration_sec']:.2f}\"" in html
        assert "mg_anim_0" in html


class TestFallbackHitsTemplateLibrary:
    """FallbackEngine.find_best_template 对真实模板库的关键词命中。"""

    @pytest.mark.parametrize("desc,expected", [
        ("产品A和B的性能对比分析", "mg_comparison_split"),
        ("需要一个科技感的大标题开头", "mg_title_reveal"),
        ("展示项目完成进度的动画", "mg_timeline_progress"),
        ("展示各季度销售数据图表", "mg_data_bars"),
        ("用户数量增长统计数字", "mg_counter_up"),
        ("讲解产品开发流程步骤", "mg_flow_arrows"),
        ("展示一句名人金句格言", "mg_quote_card"),
        ("画一个知识体系思维导图", "mg_mindmap"),
    ])
    def test_keyword_hit(self, desc: str, expected: str) -> None:
        templates = list_templates()
        result = FallbackEngine.find_best_template(desc, templates)
        assert result is not None, f"no template matched for: {desc}"
        assert result["animation_id"] == expected, (
            f"{desc!r} -> {result['animation_id']}, expected {expected}"
        )
