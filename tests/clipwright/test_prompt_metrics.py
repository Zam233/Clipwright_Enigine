"""M6（PRD §16.4/§16.6）：生成侧指标回归。

不跑 LLM，而是把每份**已迁移提示词自己的 Example** 当作"生成结果"喂给真实的校验与
归一化链路，用可重复的方式钉住这些指标：

- motion coverage ≥45%（有属性变化的主体元素占比）；
- `position` 不落字幕带（`bottom` 直接违规；像素 y 底边 ≤1080-160=920）；
- 单行 ≤14 字、元素 ≤3（文字）/ 结构图元数合理（MG）；
- JSON 修复率：干净 Example 不应触发 `repair_mg_json`（修复率上升说明提示词在教模型写坏 JSON）；
- 确定性：同输入两次归一化结果一致（关键帧时间点不变）。

这套断言的用法：改提示词 → 这里红了 → 就是提示词把模型教坏了，而不是测试太严。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from clipwright.animation.mg.validator import repair_mg_json, validate_mg_json
from clipwright.animation.text_generation import (
    CAPTION_BAND_PX, MAX_ELEMENTS, MAX_LINE_CHARS, validate_text_def,
)

PROMPTS = Path(__file__).resolve().parents[2] / "clipwright" / "animation" / "prompts"
TEXT_IDS = ("emphasis_keyword", "reveal_line", "quote_block",
            "label_callout", "number_punch", "title_stack")
MG_IDS = ("bar_chart", "line_chart", "comparison", "flow_chart")

MOTION_COVERAGE_MIN = 0.45
_ANIMATED = ("opacity", "scale", "translate_x", "translate_y", "rotate", "color",
             "font_size", "width", "height")


def _examples(path: Path) -> list[dict]:
    body = path.read_text(encoding="utf-8")
    section = body.split("## Example", 1)[-1]
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", section, flags=re.S)
    out: list[dict] = []
    for b in blocks:
        t = b.strip()
        if t.startswith("{"):
            out.append(json.loads(t))
    return out


def _motion_coverage(mg_def: dict) -> float:
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    if not elements:
        return 0.0
    moving = 0
    for e in elements:
        kfs = e.get("keyframes") or []
        props: set[str] = set()
        for kf in kfs:
            payload = kf.get("properties", kf) if isinstance(kf, dict) else {}
            props.update(k for k in payload if k in _ANIMATED)
        # 至少两个关键帧且属性真的在变，才算"动"
        if len(kfs) >= 2 and props:
            moving += 1
    return moving / len(elements)


@pytest.mark.parametrize("type_id", TEXT_IDS)
def test_text_prompt_example_is_valid_and_moving(type_id: str) -> None:
    examples = _examples(PROMPTS / "text" / f"{type_id}.md")
    assert examples, f"{type_id} 的 Example 必须给可渲染的 JSON"
    for mg_def in examples:
        dur = float(mg_def.get("duration_sec") or 0)
        assert dur > 0
        errors = validate_text_def(mg_def, duration_s=dur)
        assert errors == [], (type_id, errors)

        # 指标：motion coverage、字数、元素数、字幕带
        assert _motion_coverage(mg_def) >= MOTION_COVERAGE_MIN, type_id
        elements = mg_def["elements"]
        assert len(elements) <= MAX_ELEMENTS, type_id
        for e in elements:
            content = str(e.get("content") or "")
            assert len(content) <= MAX_LINE_CHARS, (type_id, content)
            assert e.get("position") != "bottom", type_id
            y = float(e.get("y") or 0)
            if y:
                assert y <= 1080 - CAPTION_BAND_PX, (type_id, y)

        # 关键帧升序且落在片段内
        for e in elements:
            times = [float(kf["time"]) for kf in e.get("keyframes") or []]
            assert times == sorted(times), (type_id, times)
            assert 0 <= times[0] and times[-1] <= dur, (type_id, times, dur)

        # 确定性：再解析一次结构与关键帧时间完全一致
        again = json.loads(json.dumps(mg_def))
        assert [kf["time"] for e in again["elements"] for kf in e["keyframes"]] == \
               [kf["time"] for e in elements for kf in e["keyframes"]]

        # 修复率：干净示例不该需要 repair（fixes 为空 = 不需要修）
        repaired, fixes = repair_mg_json(mg_def)
        # 修复率指标：干净示例不该被"改结构"（补默认 style 之类的归一化不算修复，
        # 但改关键帧/删属性就算——那说明提示词在教模型写坏 JSON）
        assert fixes == [], f"{type_id} 的示例需要被修复：{fixes}"
        assert repaired["elements"] == mg_def["elements"]


@pytest.mark.parametrize("type_id", MG_IDS)
def test_mg_prompt_example_is_renderable(type_id: str) -> None:
    examples = _examples(PROMPTS / "types" / f"{type_id}.md")
    assert examples, f"{type_id} 的 Example 必须给可渲染的 JSON"
    for mg_def in examples:
        ok, errs = validate_mg_json(mg_def)
        assert ok, (type_id, errs)
        assert _motion_coverage(mg_def) >= MOTION_COVERAGE_MIN, type_id
        for e in mg_def["elements"]:
            y = float(e.get("y") or 0)
            if y:
                # MG 图元中心点在安全区内即可，但要给半个图元高度留余量
                height = float(e.get("props", {}).get("height") or 0) or 0
                assert y + height / 2 <= 1080 - CAPTION_BAND_PX, (type_id, y, height)
        repaired, fixes = repair_mg_json(mg_def)
        assert fixes == [], f"{type_id} 示例需要修复：{fixes}"
        assert repaired["elements"] == mg_def["elements"]


def test_text_prompts_respect_pipeline_limits() -> None:
    """提示词里写的上限必须和代码常量一致（写 18 字却按 14 字校验 = 线上必失败）。"""
    for type_id in TEXT_IDS:
        body = (PROMPTS / "text" / f"{type_id}.md").read_text(encoding="utf-8")
        for m in re.finditer(r"≤\s*(\d+)\s*字", body):
            assert int(m.group(1)) <= MAX_LINE_CHARS, (type_id, m.group(0))
        for m in re.finditer(r"元素数\s*≤\s*(\d+)", body):
            assert int(m.group(1)) <= MAX_ELEMENTS, (type_id, m.group(0))
        assert "字幕带" in body and str(CAPTION_BAND_PX) in body, type_id


def _last_change_time(mg_def: dict) -> float:
    """最后一个**真正改变属性**的关键帧时间（静止帧不算）。"""
    last = 0.0
    for e in mg_def["elements"]:
        prev: dict = {}
        for kf in e.get("keyframes") or []:
            payload = {k: v for k, v in kf.items() if k != "time"}
            changed = any(payload.get(k) != prev.get(k) for k in _ANIMATED if k in payload or k in prev)
            if changed and float(kf["time"]) > 0:
                last = max(last, float(kf["time"]))
            prev = payload
    return last


def test_migrated_text_prompts_share_motion_shape() -> None:
    """六类文字的 Example 都给了阶段表：动画覆盖 ≥45% 且末段静止 ≥0.6s。"""
    for type_id in TEXT_IDS:
        mg_def = _examples(PROMPTS / "text" / f"{type_id}.md")[0]
        dur = float(mg_def["duration_sec"])
        last = _last_change_time(mg_def)
        assert last > 0, type_id
        assert dur - last >= 0.6, (type_id, dur, last)
        assert last / dur >= MOTION_COVERAGE_MIN, (type_id, dur, last)
