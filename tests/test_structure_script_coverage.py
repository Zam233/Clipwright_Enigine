"""分镜必须覆盖文稿，已确认场景表残缺时不得盲目复用（E2E 2026-10-28）。

现象（702.336s 配音 / 4203 字文稿）：
- structure 阶段给出 6 个场景，口播只覆盖开头几段（约 5% 文稿）；
- 这份场景表进了 `production_plan.raw_scenes`，之后 **每次** dispatch structure
  都被「复用已确认规划书」无条件接管；
- 主 agent 两次要求「忽略残次场景表、按全文逐段重排」都被复用分支吞掉，
  任务最终 `task_end=failed`（55 分钟内无法推进）。

修复后的契约：
1. 复用的最低覆盖度 `_MIN_REUSE_COVERAGE`：低于它 → 拒绝复用，改为重新生成；
2. 重新生成后覆盖度仍低于 `_MIN_ACCEPT_COVERAGE` → 用 `_scenes_from_script`
   按段落/标点确定性切分兜底（覆盖率接近 100%，时长按配音总长分配）；
3. 没有文稿时一切照旧（coverage=1.0，不做任何判断）。
"""

from __future__ import annotations

import pytest

from clipwright.agents.structure_agent import (
    _MIN_ACCEPT_COVERAGE,
    _MIN_REUSE_COVERAGE,
    _scenes_from_script,
    _script_coverage,
    _split_script_paragraphs,
)

SCRIPT = (
    "第一段开场白，交代事件起因。\n"
    "第二段展开论证，说明时间线。\n"
    "第三段给出结论，收束全片。\n"
) * 12  # 36 段，远多于 6 场景


def _scenes_with_voice(*voices: str) -> list[dict]:
    return [
        {"title": f"s{i}", "description": "d", "duration_sec": 10,
         "keywords": ["k"], "voiceover_script": v}
        for i, v in enumerate(voices)
    ]


def test_coverage_is_one_without_script():
    assert _script_coverage(_scenes_with_voice("a"), "") == 1.0
    assert _script_coverage([], "有文稿") == 0.0


def test_partial_scenes_are_detected_as_low_coverage():
    """6 个场景只覆盖开头一小段 → 必须在复用阈值之下。"""
    scenes = _scenes_with_voice(*[SCRIPT[:20]] * 6)

    assert _script_coverage(scenes, SCRIPT) < _MIN_REUSE_COVERAGE


def test_full_scenes_pass_coverage():
    scenes = _scenes_with_voice(SCRIPT)

    assert _script_coverage(scenes, SCRIPT) >= _MIN_REUSE_COVERAGE


def test_split_keeps_every_paragraph():
    chunks = _split_script_paragraphs(SCRIPT)

    assert len(chunks) >= 30, "逐段切分不能把多段合并成几段"
    assert "".join(chunks).count("第一段开场白") == 12, "内容不能丢"


def test_long_paragraph_is_split_by_punctuation():
    long_para = "这是一句话。" * 60  # 240+ 字

    chunks = _split_script_paragraphs(long_para)

    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks), "单场景口播要切到软上限内"


def test_scenes_from_script_cover_script_and_fill_audio_duration():
    scenes = _scenes_from_script(SCRIPT, audio_duration_sec=100.0)

    assert scenes, "有文稿就必须切出场景"
    assert _script_coverage(scenes, SCRIPT) > _MIN_ACCEPT_COVERAGE
    assert sum(s["duration_sec"] for s in scenes) == pytest.approx(100.0, abs=0.5)
    for s in scenes:
        assert s["title"] and s["description"] and s["voiceover_script"]
        assert s["duration_sec"] > 0
        assert isinstance(s["keywords"], list) and s["keywords"]


def test_scenes_from_script_without_audio_uses_char_rate():
    scenes = _scenes_from_script(SCRIPT, audio_duration_sec=0)

    charted = sum(len(c) for c in _split_script_paragraphs(SCRIPT))
    total = sum(s["duration_sec"] for s in scenes)
    assert total == pytest.approx(charted / 4.5, rel=0.05)


def test_empty_script_yields_nothing():
    assert _scenes_from_script("   ", 100) == []
