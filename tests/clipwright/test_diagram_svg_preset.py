"""DiagramStyle style-preset 单元测试（B13：仅传 dataclass 字段构造）。"""

from __future__ import annotations

import pytest

from clipwright.animation.diagram_svg import DiagramStyle
from clipwright.plugins.hooks import HookPoint, HookRegistry

TEST_PRESETS: dict[str, dict] = {
    "gold_black": {
        "primary_color": "#d4a843",
        "secondary_color": "#8b6f2e",
        "accent_color": "#f0c860",
        "text_color": "#f5e6c8",
        "bg_color": "rgba(0,0,0,0.35)",
        "stagger_delay": 0.3,
    },
}


def _preset_hook(context: dict) -> dict:
    return {"presets": TEST_PRESETS}


@pytest.fixture(autouse=True)
def _clean_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离 DIAGRAM_STYLE_PRESET 钩子，确保测试确定性。"""
    monkeypatch.setattr(HookRegistry, "_hooks", {hp: [] for hp in HookPoint})
    HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, _preset_hook)


class TestDiagramStylePreset:
    """DiagramStyle.from_persona 应用 style preset 的行为。"""

    def test_happy_preset_path(self) -> None:
        """指定 style_preset 时应应用 preset 字段且不抛 TypeError。"""
        style = DiagramStyle.from_persona({"style_preset": "gold_black"})
        assert isinstance(style, DiagramStyle)
        assert style.primary_color == "#d4a843"
        assert style.secondary_color == "#8b6f2e"
        assert style.accent_color == "#f0c860"
        assert style.text_color == "#f5e6c8"
        assert style.stagger_delay == 0.3

    def test_no_preset(self) -> None:
        """未指定 preset 时应返回默认实例。"""
        style = DiagramStyle.from_persona({})
        assert isinstance(style, DiagramStyle)
        assert style.primary_color == "#4f8cff"
        assert style.stagger_delay == 0.25

    def test_mixed_explicit_overrides_win(self) -> None:
        """preset + 显式字段覆盖：显式值应胜出。"""
        style = DiagramStyle.from_persona(
            {
                "style_preset": "gold_black",
                "primary_color": "#ff0000",
                "font_size": 40,
            }
        )
        assert style.primary_color == "#ff0000"
        assert style.font_size == 40
        # preset 中未被显式覆盖的字段仍生效
        assert style.secondary_color == "#8b6f2e"

    def test_fields_not_in_preset_keep_defaults(self) -> None:
        """preset 未覆盖的字段应保留默认值。"""
        style = DiagramStyle.from_persona({"style_preset": "gold_black"})
        assert style.font_size == 28
        assert style.title_font_size == 36
        assert style.border_radius == 12
        assert style.arrow_color == "#4f8cff"
