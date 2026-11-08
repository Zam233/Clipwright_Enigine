"""结构 prompt 的场景数必须由文稿长度决定（E2E 2026-10-28，长文稿只出 6 场）。

现象：702.336s 配音 / 4203 字（40+ 段）文稿，结构 LLM 只给 6 个场景，
每个场景背 100s 口播，正文只覆盖开头几段。根因是 system prompt 里硬写
「1. 4-6 个场景」——模型完全按字面执行。

修复后的契约：
1. 有文稿且段落数 ≥4 → 明确要求「一段一个场景」，并给出建议场景数；
2. voiceover_script 必须逐字照抄对应段落，合起来覆盖全文；
3. 给出目标时长时，要求 duration 之和接近目标、按字数比例分配；
4. 没有文稿（或段落很少）→ 保持原来的 4-6 个场景口径。
"""

from __future__ import annotations

from clipwright.agents.structure_agent import StructureAgent


def _prompt(**kwargs) -> str:
    return StructureAgent._build_system_prompt(  # noqa: SLF001
        {"tone": "冷峻"}, {"academic_density": 0.2}, {"cut_profile": "fast_cut"},
        {"max_duration_sec": 900}, **kwargs,
    )


def test_long_script_asks_for_one_scene_per_paragraph():
    text = _prompt(paragraph_count=43, target_sec=702.0)

    assert "覆盖全文" in text
    assert "43 段" in text
    assert "一段一个场景" in text
    assert "4-6 个场景" not in text, "长文稿不能被 4-6 个场景的常量钉死"
    assert "voiceover_script 必须是该段文稿的原话" in text
    assert "702s" in text


def test_short_script_keeps_small_scene_count():
    text = _prompt(paragraph_count=0)

    assert "4-6 个场景" in text
    assert "覆盖全文" not in text


def test_few_paragraphs_keep_small_scene_count():
    text = _prompt(paragraph_count=3)

    assert "4-6 个场景" in text


def test_target_sec_optional():
    text = _prompt(paragraph_count=30, target_sec=0)

    assert "一段一个场景" in text
    assert "duration_sec 之和应接近" not in text
