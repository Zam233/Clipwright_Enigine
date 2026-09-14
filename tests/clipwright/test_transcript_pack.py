"""短语级转写打包（V6）测试。

验收目标（报告 §1.1 V6）：同一素材，"打包后的短语视图"相对"原始逐词 JSON"
**字符数下降 ≥60%**（token 近似同比例），且切点候选（静音区间）不丢。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services.transcript_pack import (
    DEFAULT_GAP_SEC,
    TranscriptPack,
    TranscriptPhrase,
    TranscriptUnit,
    estimate_tokens,
    pack_phrases,
    pack_segments,
    pack_transcript,
    render_packed,
    silence_windows,
)


def _word_level(n_words: int = 300, *, words_per_second: float = 3.0,
                pause_every: int = 20, pause_sec: float = 0.8) -> list[dict]:
    """造一段"逐词 ASR 输出"：每词一个 JSON 单元，每 N 个词一次停顿。"""
    units: list[dict] = []
    t = 0.0
    for i in range(n_words):
        duration = 1.0 / words_per_second
        units.append({
            "text": f"词{i}",
            "start": round(t, 3),
            "end": round(t + duration, 3),
            "confidence": 0.92,
            "speaker": "S1",
        })
        t += duration
        if (i + 1) % pause_every == 0:
            t += pause_sec
    return units


class TestCoercionAndBasics:
    def test_accepts_segment_shape_from_stt(self) -> None:
        """STT 的 segments（start/end/text/confidence，无 speaker）可直接喂进来。

        注意语义：句级输入之间若只隔 0.1s（没有真正停顿），按规则仍算同一短语——
        "短语"的边界是**停顿/换人/超长**，不是原输入的切分。
        """
        phrases = pack_phrases([
            {"start": 0.0, "end": 2.0, "text": "第一句", "confidence": 0.5},
            {"start": 2.1, "end": 4.0, "text": "第二句", "confidence": 0.5},
        ])
        assert len(phrases) == 1
        assert phrases[0].text == "第一句第二句"
        assert phrases[0].start == 0.0 and phrases[0].end == 4.0

    def test_stt_segments_with_real_pause_split(self) -> None:
        phrases = pack_phrases([
            {"start": 0.0, "end": 2.0, "text": "第一句"},
            {"start": 3.0, "end": 4.0, "text": "第二句"},
        ])
        assert [p.text for p in phrases] == ["第一句", "第二句"]

    def test_accepts_alternative_key_names(self) -> None:
        phrases = pack_phrases([
            {"word": "hello", "start_sec": 0.0, "end_sec": 0.4},
            {"word": "world", "start_sec": 0.4, "end_sec": 0.9},
        ])
        assert len(phrases) == 1
        assert "hello" in phrases[0].text and "world" in phrases[0].text

    def test_garbage_is_skipped_not_fatal(self) -> None:
        phrases = pack_phrases([
            {"text": "有效", "start": 0.0, "end": 1.0},
            {"text": "无时间", "start": "坏", "end": "坏"},
            {"text": "", "start": 1.0, "end": 2.0},
        ])
        assert [p.text for p in phrases] == ["有效"]
        # 完全非法的输入直接返回空表而不是抛异常
        assert pack_phrases([None, "x", {}, {"text": "  "}]) == []  # type: ignore[list-item]

    def test_unsorted_input_is_sorted(self) -> None:
        phrases = pack_phrases([
            {"text": "后", "start": 5.0, "end": 6.0},
            {"text": "前", "start": 0.0, "end": 1.0},
        ])
        assert [p.text for p in phrases] == ["前", "后"]

    def test_reversed_times_are_swapped(self) -> None:
        phrases = pack_phrases([{"text": "x", "start": 3.0, "end": 1.0}])
        assert phrases[0].start == 1.0 and phrases[0].end == 3.0


class TestPhraseBoundaries:
    def test_merges_contiguous_words(self) -> None:
        phrases = pack_phrases([{"text": w, "start": i * 0.3, "end": i * 0.3 + 0.3}
                                for i, w in enumerate(["我", "想", "要"])])
        assert len(phrases) == 1
        assert phrases[0].text == "我想要"
        assert phrases[0].units == 3

    def test_breaks_on_silence(self) -> None:
        units = [
            {"text": "前半", "start": 0.0, "end": 1.0},
            {"text": "后半", "start": 1.0 + DEFAULT_GAP_SEC, "end": 3.0},
        ]
        phrases = pack_phrases(units)
        assert [p.text for p in phrases] == ["前半", "后半"]

    def test_short_pause_does_not_break(self) -> None:
        units = [
            {"text": "前半", "start": 0.0, "end": 1.0},
            {"text": "后半", "start": 1.3, "end": 3.0},   # 0.3s < 0.5s
        ]
        assert len(pack_phrases(units)) == 1

    def test_breaks_on_speaker_change(self) -> None:
        units = [
            {"text": "甲说", "start": 0.0, "end": 1.0, "speaker": "S1"},
            {"text": "乙说", "start": 1.0, "end": 2.0, "speaker": "S2"},
        ]
        phrases = pack_phrases(units)
        assert [p.speaker for p in phrases] == ["S1", "S2"]

    def test_breaks_on_duration_cap(self) -> None:
        units = [{"text": "字", "start": i * 1.0, "end": i * 1.0 + 1.0} for i in range(12)]
        phrases = pack_phrases(units, max_duration=3.0)
        assert all(p.duration <= 3.0 for p in phrases)
        assert len(phrases) == 4

    def test_breaks_on_char_cap(self) -> None:
        units = [{"text": "一二三四五", "start": i * 0.5, "end": i * 0.5 + 0.5} for i in range(6)]
        phrases = pack_phrases(units, max_chars=12)
        assert all(len(p.text) <= 12 for p in phrases)

    def test_custom_joiner(self) -> None:
        phrases = pack_phrases(
            [{"text": "hello", "start": 0.0, "end": 0.3}, {"text": "world", "start": 0.3, "end": 0.6}],
            joiner=" ",
        )
        assert phrases[0].text == "hello world"


class TestSilenceWindows:
    def test_detects_head_between_and_tail(self) -> None:
        units = [
            {"text": "a", "start": 1.2, "end": 2.0},
            {"text": "b", "start": 3.5, "end": 4.0},
        ]
        windows = silence_windows(units, total_duration=6.0)
        kinds = [w["where"] for w in windows]
        assert kinds == ["head", "between", "tail"]
        assert windows[1]["duration"] == pytest.approx(1.5)
        assert windows[2]["start"] == 4.0 and windows[2]["end"] == 6.0

    def test_short_gaps_ignored(self) -> None:
        units = [{"text": "a", "start": 0.0, "end": 1.0}, {"text": "b", "start": 1.2, "end": 2.0}]
        assert silence_windows(units) == []

    def test_empty_input(self) -> None:
        assert silence_windows([]) == []
        assert silence_windows([], total_duration=10.0) == []


class TestRenderAndCompression:
    def test_render_format(self) -> None:
        phrases = [
            TranscriptPhrase(start=0.0, end=1.5, text="第一句", speaker="S1"),
            TranscriptPhrase(start=2.0, end=4.0, text="第二句"),
        ]
        text = render_packed(phrases)
        assert text == "[0.0-1.5] S1 第一句\n[2.0-4.0] 第二句"

    def test_compression_at_least_60_percent(self) -> None:
        """验收：逐词 JSON → 短语视图，字符数下降 ≥60%。"""
        units = _word_level(300)
        pack = pack_transcript(units, total_duration=units[-1]["end"])

        assert pack.phrases, "应产出短语"
        assert pack.raw_chars > 0
        assert pack.packed_chars > 0
        drop = 1 - pack.compression
        assert drop >= 0.60, f"压缩率不足：{drop:.1%}（raw={pack.raw_chars} packed={pack.packed_chars}）"

    def test_token_estimate_drops_too(self) -> None:
        units = _word_level(300)
        pack = pack_transcript(units)
        raw_text = json.dumps(units, ensure_ascii=False)
        assert estimate_tokens(pack.text) < estimate_tokens(raw_text) * 0.4

    def test_compression_of_empty_input(self) -> None:
        pack = pack_transcript([])
        assert pack.phrases == []
        assert pack.text == ""
        assert pack.compression == 0.0

    def test_silences_survive_packing(self) -> None:
        """静音区间（切点候选）不能因为打包而丢失。"""
        units = _word_level(60)
        pack = pack_transcript(units, total_duration=units[-1]["end"])
        # 每 20 词一次 0.8s 停顿 → 2 处
        assert len(pack.silences) == 2
        assert all(w["duration"] >= 0.5 for w in pack.silences)

    def test_raw_payload_override(self) -> None:
        units = [{"text": "a", "start": 0.0, "end": 1.0}]
        pack = pack_transcript(units, raw_payload={"huge": "x" * 5000})
        assert pack.raw_chars > 5000
        assert pack.compression < 0.01

    def test_speaker_mapping_callback(self) -> None:
        units = [{"text": "a", "start": 0.0, "end": 1.0}, {"text": "b", "start": 1.0, "end": 2.0}]
        pack = pack_transcript(units, speaker_of=lambda u: "S1" if u.start < 1.0 else "S2")
        assert [p.speaker for p in pack.phrases] == ["S1", "S2"]

    def test_to_dict_is_json_serializable(self) -> None:
        pack = pack_transcript(_word_level(40))
        payload = pack.to_dict()
        assert json.loads(json.dumps(payload, ensure_ascii=False))["phrase_count"] == payload["phrase_count"]
        assert payload["compression"] == round(pack.compression, 4)

    def test_pack_segments_entrypoint(self) -> None:
        pack: TranscriptPack = pack_segments([
            {"start": 0.0, "end": 2.0, "text": "一", "confidence": 0.5},
            {"start": 3.0, "end": 5.0, "text": "二", "confidence": 0.5},
        ], total_duration=6.0)
        assert len(pack.phrases) == 2
        assert pack.silences[-1]["where"] == "tail"


class TestTokenEstimator:
    def test_cjk_counts_as_one_token(self) -> None:
        assert estimate_tokens("你好世界") == pytest.approx(4.0)

    def test_latin_words(self) -> None:
        assert estimate_tokens("hello world") == pytest.approx(2 * 1.3)

    def test_empty(self) -> None:
        assert estimate_tokens("") == 0.0

    def test_mixed(self) -> None:
        value = estimate_tokens("帧艺 ClipWright 1.0")
        assert value > 2
