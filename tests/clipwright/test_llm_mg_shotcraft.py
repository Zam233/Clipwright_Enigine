"""llm_mg Shotcraft 动效风格指引 + config 契约单元测试。

验证 mg/config.yaml 的 system_template 已注入 video-shotcraft 镜头卡
方法论，且 keyframes 白名单（可用动画属性）保持不变、JSON schema 契约完好。
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "mg" / "config.yaml"

# 快照：config.yaml 的 keyframes 白名单（可用动画属性）。
# 修改 config.yaml 时若变更白名单，必须同步更新此快照，否则测试失败。
# 合并更新（2026-09）：白名单并入本地线的 easing（逐关键帧缓动）与
# font_weight（字重）——均已包含在 validator.ANIMATABLE_PROPS 契约内。
EXPECTED_WHITELIST = {
    "opacity", "scale", "translate_x", "translate_y", "rotate",
    "width", "height", "font_size", "color", "border_radius",
    "box_shadow", "text_shadow", "filter", "letter_spacing",
    "background", "easing", "font_weight", "line_height", "transform_origin",
}

SECTION_TITLE = "动效风格指引（video-shotcraft 镜头卡方法论）"


def _load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _system_template() -> str:
    return _load_config()["prompt"]["system_template"]


def _extract_whitelist(template: str) -> set[str]:
    """从 template 的「可用动画属性」段落提取属性名。"""
    props: set[str] = set()
    in_section = False
    for line in template.splitlines():
        stripped = line.strip()
        if stripped.startswith("## 可用动画属性"):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                body = stripped[2:].split(":", 1)[0]
                for token in body.split("/"):
                    name = token.split("(")[0].strip()
                    if name:
                        props.add(name)
    return props


def _shotcraft_section(template: str) -> str:
    """返回 shotcraft 动效风格指引段落的完整文本。"""
    idx = template.index(SECTION_TITLE)
    line_start = template.rfind("\n", 0, idx) + 1
    end = template.find("\n## ", line_start)
    if end == -1:
        end = len(template)
    return template[line_start:end]


class TestConfigYamlLoads:
    """config.yaml 必须始终是合法 YAML。"""

    def test_yaml_safe_load_succeeds(self) -> None:
        cfg = _load_config()
        assert isinstance(cfg, dict)
        assert cfg["prompt"]["system_template"]

    def test_system_template_is_substantial(self) -> None:
        template = _system_template()
        assert isinstance(template, str)
        assert len(template) > 100


class TestShotcraftGuidanceInPrompt:
    """shotcraft 动效风格指引已注入 system_template。"""

    def test_section_title_present(self) -> None:
        template = _system_template()
        assert SECTION_TITLE in template

    def test_section_contains_hold_and_stagger_keywords(self) -> None:
        section = _shotcraft_section(_system_template())
        assert "hold" in section
        assert "0.2-0.5s" in section

    def test_section_contains_core_principles(self) -> None:
        section = _shotcraft_section(_system_template())
        for keyword in ("单镜头单主角", "hold", "0.2-0.5s", "0.5s", "ease-out", "1.15"):
            assert keyword in section, f"section missing keyword: {keyword}"


class TestKeyframeWhitelistUnchanged:
    """keyframes 白名单（config.yaml:77-124 区域）与快照保持一致。"""

    def test_whitelist_matches_snapshot(self) -> None:
        assert _extract_whitelist(_system_template()) == EXPECTED_WHITELIST

    def test_whitelist_is_subset_of_validator_contract(self) -> None:
        from clipwright.animation.mg.validator import ANIMATABLE_PROPS
        assert _extract_whitelist(_system_template()) <= ANIMATABLE_PROPS
