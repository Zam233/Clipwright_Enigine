"""MG config.yaml 加载与模板字段测试。

验证重写后的 system_template 包含专业动效设计原则、逐关键帧 easing 说明、
元素种类、硬性约束，并内嵌 2 个可解析且可校验的 few-shot 示例 JSON。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from clipwright.animation.mg.validator import validate_mg_json

CONFIG_PATH = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "mg" / "config.yaml"


def _load_template() -> str:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg["prompt"]["system_template"]


def _extract_examples(tpl: str) -> list[dict]:
    """提取 system_template 内嵌的完整 JSON 示例。"""
    anchors = [m.start() for m in re.finditer(r'\{\s*"animation_id"', tpl)]
    examples: list[dict] = []
    for s in anchors:
        depth = 0
        i = s
        instr = False
        while i < len(tpl):
            c = tpl[i]
            if c == '"' and (i == 0 or tpl[i - 1] != "\\"):
                instr = not instr
            if not instr:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        raw = tpl[s:i + 1]
        try:
            examples.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return examples


class TestMGConfig:
    """config.yaml system_template 字段测试。"""

    def test_config_loads(self) -> None:
        """config.yaml 可被 yaml 安全加载且包含 prompt.system_template。"""
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        assert isinstance(cfg, dict)
        assert "prompt" in cfg
        assert "system_template" in cfg["prompt"]
        assert len(cfg["prompt"]["system_template"]) > 2000

    def test_template_covers_motion_principles(self) -> None:
        """system_template 覆盖专业动效设计原则关键词。"""
        tpl = _load_template()
        for keyword in [
            "Easing", "easing-first", "Anticipation", "Staging",
            "Stagger", "Hierarchy", "Follow-through", "Glow", "Impact",
            "back-out", "elastic-out", "bounce", "cubic-bezier",
            "text_shadow", "box_shadow", "linear-gradient",
        ]:
            assert keyword in tpl, f"missing keyword: {keyword}"

    def test_template_documents_easing_field(self) -> None:
        """逐关键帧 easing 字段被文档化。"""
        tpl = _load_template()
        assert "easing" in tpl
        for curve in ["linear", "ease", "ease-in", "ease-out", "ease-in-out",
                      "back-out", "elastic-out", "bounce"]:
            assert f'"{curve}"' in tpl, f"missing easing curve: {curve}"

    def test_template_documents_element_catalog(self) -> None:
        """元素种类目录完整。"""
        tpl = _load_template()
        for etype in ["text", "shape", "line", "circle", "ring", "arc", "bg"]:
            assert etype in tpl, f"missing element type: {etype}"

    def test_template_documents_constraints(self) -> None:
        """硬性约束（元素数/关键帧数/easing 数/time 上限/默认禁止背景层）。"""
        tpl = _load_template()
        assert "至少 3 个关键帧" in tpl
        assert "opacity:0" in tpl
        assert "元素数量 >= 3" in tpl
        assert "至少 2 个关键帧带 easing" in tpl
        assert "duration_sec" in tpl
        # T3：默认禁止不透明背景层（动画叠加实拍画面）
        assert "禁止背景层" in tpl

    def test_two_embedded_examples_parse_and_validate(self) -> None:
        """内嵌 2 个完整 few-shot 示例，且全部通过 validator。"""
        examples = _extract_examples(_load_template())
        assert len(examples) == 2
        for ex in examples:
            ok, errors = validate_mg_json(ex)
            assert ok, f"embedded example invalid: {errors}"
            assert "easing" in json.dumps(ex)
            # T3：示例不得使用不透明全幅背景（bg 背景必须为 transparent）
            bg_elems = [e for e in ex.get("elements", []) if e.get("type") == "bg"]
            for bg in bg_elems:
                assert bg.get("background") in (None, "transparent"), (
                    f"embedded example has opaque bg background: {bg.get('background')}"
                )
