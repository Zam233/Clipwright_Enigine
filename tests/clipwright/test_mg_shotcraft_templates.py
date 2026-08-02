"""video-shotcraft 镜头卡移植的 MG 模板测试 — 校验通过且可渲染 HTML。"""

from __future__ import annotations

import json
from pathlib import Path

from clipwright.animation.mg.validator import validate_mg_json
from clipwright.animation.mg_renderer import MGRenderer

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "mg" / "templates"

SHOTCRAFT_TEMPLATES = [
    "mg_spotlight_hero_card.json",
    "mg_row_embed.json",
    "mg_deck_deal_flyin.json",
]


def _load(name: str) -> dict:
    with (TEMPLATES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


class TestShotcraftTemplates:
    """三张镜头卡模板均满足 schema 契约且能渲染。"""

    def test_all_templates_exist(self) -> None:
        for name in SHOTCRAFT_TEMPLATES:
            assert (TEMPLATES_DIR / name).is_file(), f"missing template {name}"

    def test_each_template_validates(self) -> None:
        for name in SHOTCRAFT_TEMPLATES:
            ok, errors = validate_mg_json(_load(name))
            assert ok is True, f"{name} failed: {errors}"

    def test_duration_within_range(self) -> None:
        for name in SHOTCRAFT_TEMPLATES:
            dur = _load(name)["duration_sec"]
            assert 3.0 <= dur <= 4.0, f"{name} duration {dur} out of range"

    def test_first_keyframe_hidden_and_last_at_end(self) -> None:
        for name in SHOTCRAFT_TEMPLATES:
            d = _load(name)
            dur = d["duration_sec"]
            for i, elem in enumerate(d["elements"]):
                kfs = elem["keyframes"]
                assert kfs[0]["time"] == 0 and kfs[0].get("opacity", 1) == 0, (
                    f"{name} elements[{i}] first keyframe not hidden at time 0"
                )
                assert kfs[-1]["time"] == dur, (
                    f"{name} elements[{i}] last keyframe != duration"
                )

    def test_each_template_renders_nonempty_html(self) -> None:
        for name in SHOTCRAFT_TEMPLATES:
            html = MGRenderer.render(_load(name))
            assert isinstance(html, str) and len(html) > 0, f"{name} rendered empty html"
