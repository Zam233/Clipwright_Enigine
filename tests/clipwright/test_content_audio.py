"""R3：音频维度测试（音频事件 / 转写对齐 / 关键词 / 事件词表）。

分组：
1. **信号层**：WAV 读取、短时能量、静音/大响段/起始沿检测、事件合并（用合成信号，无 ffmpeg）；
2. **对齐层**：片段归一化（多种键名）、按重叠落到镜头、跨镜头切分、统计；
3. **文本层**：关键词抽取（无分词器依赖）、领域词表（合并 generic/追加 JSON）、事件定位与证据；
4. **工具层**：`event_spot` 的 text/audio 两种模式、错误路径；
5. **接线**：`build_index` 是否把音频事件与语音写进镜头记录（打桩 + 合成素材）。
"""

from __future__ import annotations

import asyncio
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from clipwright.services import content_audio as ca
from clipwright.services.event_lexicon import (
    DOMAIN_LEXICONS,
    EVENT_LABELS,
    VALID_DOMAINS,
    resolve_lexicon,
    spot_events_in_text,
)

pytest.importorskip("numpy")

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _write_wav(path: Path, signal, sr: int = 16000) -> str:
    """把 numpy 浮点信号写成 16-bit 单声道 WAV。"""
    import numpy as np

    arr = np.clip(np.asarray(signal, dtype="float32"), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return str(path)


def _tone(freq: float, sec: float, amp: float = 0.5, sr: int = 16000):
    import numpy as np

    t = np.arange(int(sec * sr)) / sr
    return amp * np.sin(2 * math.pi * freq * t)


def _quiet(sec: float, sr: int = 16000):
    import numpy as np

    return np.zeros(int(sec * sr), dtype="float32")


# ── 1. 信号层 ─────────────────────────────────────────────


class TestWavRead:
    def test_roundtrip(self, tmp_path: Path) -> None:
        import numpy as np

        sig = np.concatenate([_quiet(0.2), _tone(440, 0.3)])
        p = _write_wav(tmp_path / "a.wav", sig)
        data, sr = ca.read_wav_mono(p)
        assert sr == 16000
        assert len(data) == len(sig)
        assert abs(float(np.abs(data).max()) - 0.5) < 0.05

    def test_missing_file(self, tmp_path: Path) -> None:
        data, sr = ca.read_wav_mono(tmp_path / "ghost.wav")
        assert data is None and sr == 0


class TestEnergy:
    def test_empty_signal(self) -> None:
        db, hop = ca.frame_energy_db(None, 16000)
        assert db.size == 0 and hop > 0

    def test_loud_is_above_quiet(self) -> None:
        import numpy as np

        sig = np.concatenate([_quiet(0.5), _tone(440, 0.5, amp=0.8)])
        db, _hop = ca.frame_energy_db(sig, 16000)
        assert db.max() - db.min() > 40  # 静音与满幅之间的动态范围

    def test_rate_independent(self) -> None:
        db16, _ = ca.frame_energy_db(_tone(200, 0.4), 16000)
        db48, _ = ca.frame_energy_db(_tone(200, 0.4), 48000)
        assert abs(float(db16.mean()) - float(db48.mean())) < 1.0


class TestAudioEvents:
    def _sig(self):
        import numpy as np

        # 静音 0.6s → 大响 0.8s → 静音 0.6s → 大响 0.5s → 静音 0.4s
        return np.concatenate([
            _quiet(0.6), _tone(300, 0.8, 0.9), _quiet(0.6), _tone(500, 0.5, 0.9), _quiet(0.4),
        ])

    def test_detects_loud_and_silence(self) -> None:
        events = ca.detect_audio_events(self._sig(), 16000)
        kinds = {ev["kind"] for ev in events}
        assert {"loud", "silence"} <= kinds
        loud = [ev for ev in events if ev["kind"] == "loud"]
        silence = [ev for ev in events if ev["kind"] == "silence"]
        assert len(loud) >= 2 and len(silence) >= 2

    def test_loud_time_range_matches_signal(self) -> None:
        events = ca.detect_audio_events(self._sig(), 16000)
        first_loud = next(ev for ev in events if ev["kind"] == "loud")
        assert first_loud["t0"] == pytest.approx(0.6, abs=0.15)
        assert first_loud["t1"] == pytest.approx(1.4, abs=0.15)

    def test_detects_onset(self) -> None:
        events = ca.detect_audio_events(self._sig(), 16000)
        assert any(ev["kind"] == "onset" for ev in events)

    def test_flat_signal_has_no_loud(self) -> None:
        """恒定电平（纯静音/纯音）不出任何事件——没有动态范围就没有"事件"。

        整段全静音这类**素材级**属性由对齐层（无 speech）与统计层表达，
        硬造一个覆盖全片的"loud/silence 事件"只会污染索引。
        """
        import numpy as np

        assert ca.detect_audio_events(np.zeros(16000, dtype="float32"), 16000) == []
        assert ca.detect_audio_events(_tone(440, 1.0, amp=0.6), 16000) == []

    def test_small_span_still_detected(self) -> None:
        """动态范围只要超过下界(3dB)就要能检出——真实素材电平差异可能很小。"""
        import numpy as np

        sig = np.concatenate([_tone(300, 0.8, amp=0.01), _tone(300, 0.8, amp=0.04)])
        events = ca.detect_audio_events(sig, 16000)
        assert events, "4 倍幅度差（≈12dB）应当能分出两段"

    def test_min_event_sec_filters_short_blips(self) -> None:
        import numpy as np

        sig = np.concatenate([_quiet(0.5), _tone(400, 0.05, 0.9), _quiet(0.5)])
        events = ca.detect_audio_events(sig, 16000, min_event_sec=0.3)
        assert not [ev for ev in events if ev["kind"] == "loud"]

    def test_empty_signal(self) -> None:
        assert ca.detect_audio_events(None, 16000) == []

    def test_thresholds_recorded(self) -> None:
        events = ca.detect_audio_events(self._sig(), 16000)
        assert events and all("thresholds" in ev for ev in events)


class TestMergeEvents:
    def test_merges_close_same_kind(self) -> None:
        merged = ca.merge_events([
            {"kind": "loud", "t0": 0.0, "t1": 0.5},
            {"kind": "loud", "t0": 0.6, "t1": 1.0},
        ], max_gap=0.2)
        assert len(merged) == 1 and merged[0]["t1"] == 1.0 and merged[0]["merged"] == 2

    def test_keeps_distant_and_different_kind(self) -> None:
        merged = ca.merge_events([
            {"kind": "loud", "t0": 0.0, "t1": 0.5},
            {"kind": "loud", "t0": 2.0, "t1": 2.5},
            {"kind": "silence", "t0": 0.6, "t1": 1.0},
        ], max_gap=0.2)
        assert len(merged) == 3

    def test_sorted_output(self) -> None:
        merged = ca.merge_events([
            {"kind": "loud", "t0": 2.0, "t1": 3.0},
            {"kind": "onset", "t0": 0.1, "t1": 0.3},
        ])
        assert [m["t0"] for m in merged] == [0.1, 2.0]


class TestEventsForShot:
    def test_overlap_with_relative_time(self) -> None:
        events = [{"kind": "loud", "t0": 1.0, "t1": 2.0}, {"kind": "loud", "t0": 9.0, "t1": 9.5}]
        got = ca.events_for_shot(events, 1.5, 3.0)
        assert len(got) == 1
        assert got[0]["rel_t0"] == 0.0 and got[0]["rel_t1"] == 0.5

    def test_no_overlap(self) -> None:
        assert ca.events_for_shot([{"kind": "loud", "t0": 0.0, "t1": 0.5}], 2.0, 3.0) == []


# ── 2. 对齐层 ─────────────────────────────────────────────


class TestSegmentNormalization:
    def test_various_key_names(self) -> None:
        rows = ca.segments_to_phrases([
            {"start": 0.0, "end": 1.0, "text": "甲"},
            {"start_sec": 1.0, "end_sec": 2.0, "content": "乙"},
            {"from": 2.0, "to": 2.5, "word": "丙", "speaker": "S1"},
        ])
        assert [r["text"] for r in rows] == ["甲", "乙", "丙"]
        assert rows[2]["speaker"] == "S1"

    def test_skips_bad_rows(self) -> None:
        rows = ca.segments_to_phrases([
            None, {"start": 0, "end": 1}, {"start": "x", "end": "y", "text": "坏"},
            {"start": 0.0, "end": 1.0, "text": "  "},
        ])
        assert rows == []

    def test_reversed_times_are_swapped(self) -> None:
        rows = ca.segments_to_phrases([{"start": 2.0, "end": 1.0, "text": "反"}])
        assert rows[0]["t0"] == 1.0 and rows[0]["t1"] == 2.0

    def test_sorted_by_time(self) -> None:
        rows = ca.segments_to_phrases([
            {"start": 5.0, "end": 6.0, "text": "后"},
            {"start": 1.0, "end": 2.0, "text": "前"},
        ])
        assert [r["text"] for r in rows] == ["前", "后"]


class TestAlignment:
    def test_single_phrase_lands_in_overlapping_shot(self) -> None:
        shots = [(0.0, 2.0), (2.0, 4.0)]
        rows, stats = ca.align_segments_to_shots(
            [{"start": 2.2, "end": 2.8, "text": "进球了"}], shots)
        assert rows[1]["text"] == "进球了" and rows[0]["text"] == ""
        assert stats["matched"] == 1 and stats["shots_with_speech"] == 1

    def test_phrase_spanning_two_shots_is_split(self) -> None:
        """跨镜头的转写要按交集切给两个镜头，而不是整段丢给一个。"""
        shots = [(0.0, 2.0), (2.0, 4.0)]
        rows, _ = ca.align_segments_to_shots(
            [{"start": 1.0, "end": 3.0, "text": "前半后半"}], shots)
        assert rows[0]["phrases"] and rows[1]["phrases"]
        assert rows[0]["phrases"][0]["t0"] == 1.0 and rows[0]["phrases"][0]["t1"] == 2.0
        assert rows[1]["phrases"][0]["t0"] == 0.0 and rows[1]["phrases"][0]["t1"] == 1.0

    def test_mid_frame_phrase_belongs_to_later_shot(self) -> None:
        """边界上的片段（2.0 起）必须落到后一个镜头——与 `shot_at` 的半开区间一致。"""
        shots = [(0.0, 2.0), (2.0, 4.0)]
        rows, _ = ca.align_segments_to_shots([{"start": 2.0, "end": 2.5, "text": "边"}], shots)
        assert rows[0]["text"] == "" and rows[1]["text"] == "边"

    def test_unmatched_phrase_counted(self) -> None:
        rows, stats = ca.align_segments_to_shots(
            [{"start": 100.0, "end": 101.0, "text": "界外"}], [(0.0, 2.0)])
        assert stats["unmatched"] == 1 and stats["shots_with_speech"] == 0

    def test_keywords_extracted_per_shot(self) -> None:
        rows, _ = ca.align_segments_to_shots(
            [{"start": 0.2, "end": 1.0, "text": "这个进球太漂亮了"}], [(0.0, 2.0)],
            lexicon={"sports": ["进球"]})
        assert "进球" in rows[0]["keywords"]

    def test_empty_inputs(self) -> None:
        rows, stats = ca.align_segments_to_shots([], [(0.0, 1.0), (1.0, 2.0)])
        assert len(rows) == 2 and stats["phrases"] == 0
        rows2, stats2 = ca.align_segments_to_shots(
            [{"start": 0.0, "end": 1.0, "text": "x"}], [])
        assert rows2 == [] and stats2["phrases"] == 1


# ── 3. 文本层 ─────────────────────────────────────────────


class TestKeywords:
    def test_empty(self) -> None:
        assert ca.keywords_from_text("") == []
        assert ca.keywords_from_text(None) == []  # type: ignore[arg-type]

    def test_cjk_bigrams(self) -> None:
        kws = ca.keywords_from_text("进球瞬间")
        assert "进球" in kws and "球瞬" in kws

    def test_ascii_words_lowercased_and_filtered(self) -> None:
        kws = ca.keywords_from_text("The GOAL was amazing")
        assert "goal" in kws and "amazing" in kws
        assert "the" not in kws and "was" not in kws

    def test_stopwords_filtered(self) -> None:
        kws = ca.keywords_from_text("这个是我们的然后那个")
        assert "这个" not in kws and "然后" not in kws

    def test_lexicon_hits_first(self) -> None:
        kws = ca.keywords_from_text("今天聊进球集锦", lexicon={"sports": ["进球集锦", "进球"]})
        assert kws[0] == "进球集锦"   # 长词优先
        assert "进球" in kws

    def test_limit_respected(self) -> None:
        assert len(ca.keywords_from_text("一二三四五六七八九十" * 3, limit=4)) <= 4


class TestLexicon:
    def test_generic_always_included(self) -> None:
        lex = resolve_lexicon("sports")
        assert "goal" in lex and "price" in lex     # 题材词 + 通用词

    def test_unknown_domain_falls_back_to_generic(self) -> None:
        assert resolve_lexicon("nonsense") == resolve_lexicon("generic")

    def test_extra_json_appends(self) -> None:
        lex = resolve_lexicon("sports", '{"goal": ["远射"], "new_type": ["新词"]}')
        assert "远射" in lex["goal"] and lex["new_type"] == ["新词"]

    def test_invalid_extra_json_ignored(self) -> None:
        assert resolve_lexicon("sports", "{oops")["goal"] == resolve_lexicon("sports")["goal"]

    def test_all_domains_have_labels(self) -> None:
        for domain, events in DOMAIN_LEXICONS.items():
            for ev in events:
                assert ev in EVENT_LABELS, f"{domain}.{ev} 缺中文名"

    def test_domains_listed(self) -> None:
        assert "sports" in VALID_DOMAINS and "generic" in VALID_DOMAINS


class TestSpotEvents:
    def test_goal_detected_with_evidence(self) -> None:
        events = spot_events_in_text("第 67 分钟，梅西打进一球，完成绝杀！",
                                     lexicon=resolve_lexicon("sports"),
                                     t0=100.0, t1=110.0)
        kinds = {ev["type"] for ev in events}
        assert "goal" in kinds
        goal = next(ev for ev in events if ev["type"] == "goal")
        assert goal["t0"] == 100.0 and goal["t1"] == 110.0
        assert "绝杀" in goal["evidence"] or "进球" in goal["evidence"] or "绝杀" in goal["matched"]
        assert goal["confidence"] > 0.4

    def test_time_range_is_the_text_range(self) -> None:
        events = spot_events_in_text("开箱", lexicon={"unbox": ["开箱"]}, t0=3.0, t1=5.0)
        assert events[0]["t0"] == 3.0 and events[0]["t1"] == 5.0

    def test_no_hits(self) -> None:
        assert spot_events_in_text("今天天气不错", lexicon=resolve_lexicon("sports")) == []

    def test_empty_text(self) -> None:
        assert spot_events_in_text("") == []

    def test_sorted_by_confidence(self) -> None:
        events = spot_events_in_text(
            "价格多少？成本很高，性价比一般", lexicon=resolve_lexicon("generic"))
        assert [e["confidence"] for e in events] == sorted(
            [e["confidence"] for e in events], reverse=True)

    def test_generic_domain_events(self) -> None:
        kinds = {ev["type"] for ev in spot_events_in_text(
            "最后总结一下，注意这里的风险", lexicon=resolve_lexicon("generic"))}
        assert {"conclusion", "caution"} <= kinds


# ── 4. 工具层 ─────────────────────────────────────────────


def _run_tool(**kwargs):
    from clipwright.tool.event_spot import EventSpotTool

    return asyncio.run(EventSpotTool().execute(**kwargs))


class TestEventSpotTool:
    def test_text_mode(self) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="text", text="这个进球太漂亮了", domain="sports")
        assert res.status == ToolStatus.SUCCESS
        assert res.output["count"] >= 1
        assert res.output["domain"] == "sports"

    def test_text_mode_from_segments(self) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="text", segments=[
            {"start": 10.0, "end": 12.0, "text": "球进了！"},
        ], domain="sports")
        assert res.status == ToolStatus.SUCCESS
        ev = res.output["events"][0]
        assert ev["t0"] == 10.0 and ev["t1"] == 12.0

    def test_text_mode_no_hit(self) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="text", text="今天天气不错")
        assert res.status == ToolStatus.SUCCESS and res.output["count"] == 0

    def test_unknown_mode(self) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="video")
        assert res.status == ToolStatus.ERROR and "未知 mode" in (res.error or "")

    def test_registered(self) -> None:
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.registry import ToolRegistry

        register_builtin_tools()
        tools = ToolRegistry.list()
        names = [getattr(t, "name", str(t)) for t in (tools.values() if isinstance(tools, dict) else tools)]
        assert "event_spot" in names

    def test_llm_schema(self) -> None:
        from clipwright.tool.event_spot import EventSpotTool

        schema = EventSpotTool().to_llm_tool()
        assert schema["name"] == "event_spot"
        assert "mode" in schema["input_schema"]["properties"]

    def test_audio_mode_missing_file(self, tmp_path: Path) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="audio", media_path=str(tmp_path / "ghost.mp4"))
        assert res.status == ToolStatus.ERROR

    @pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
    def test_audio_mode_on_synthetic_clip(self, tmp_path: Path) -> None:
        """带音轨的合成视频：应当检出大响段，并能按镜头切分。"""
        from clipwright.schema.tool import ToolStatus

        video = tmp_path / "a.mp4"
        # 0.8s 静音 + 0.8s 大响 + 0.8s 静音（视频用纯色，`-shortest` 对齐时长）
        r = subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=2.4",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=0.8",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=0.8",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=0.8",
            "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
            "-map", "0:v", "-map", "[a]", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(video),
        ], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"合成带音轨视频失败: {r.stderr[-200:]}")
        res = _run_tool(mode="audio", media_path=str(video),
                        shot_ranges=[[0.0, 1.2], [1.2, 2.4]])
        assert res.status == ToolStatus.SUCCESS, res.error
        assert res.output["summary"]["by_kind"].get("loud", 0) >= 1
        assert len(res.output["per_shot"]) == 2


# ── 5. 接线 ───────────────────────────────────────────────


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
class TestIndexWiring:
    def _video_with_audio(self, path: Path) -> None:
        r = subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:duration=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"合成失败: {r.stderr[-200:]}")

    def test_audio_events_land_in_shots(self, tmp_path: Path, monkeypatch) -> None:
        from clipwright.services import content_index as ci
        from clipwright.services import content_vision as cvmod

        lib = tmp_path / "library"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "projects", raising=False)
        monkeypatch.setattr(ci.settings, "content_index_mirror_mongo", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_clip_enabled", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_audio_events", True, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_asr", False, raising=False)

        async def _no_label(frames, **kwargs):
            return [{} for _ in frames]

        monkeypatch.setattr(cvmod, "label_shots", _no_label)

        video = tmp_path / "v.mp4"
        self._video_with_audio(video)
        idx = asyncio.run(ci.build_index(video, force=True, mirror=False))
        assert idx.shots
        assert any(shot.audio_events for shot in idx.shots), "音频事件没落进镜头"
        assert idx.sources.get("audio_events") == "short_term_rms"
        # 相对时间必须落在镜头内
        for shot in idx.shots:
            for ev in shot.audio_events:
                assert 0.0 <= ev["rel_t0"] <= shot.duration_sec + 0.2

    def test_asr_disabled_keeps_speech_empty(self, tmp_path: Path, monkeypatch) -> None:
        from clipwright.services import content_index as ci
        from clipwright.services import content_vision as cvmod

        lib = tmp_path / "library"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "projects", raising=False)
        monkeypatch.setattr(ci.settings, "content_index_mirror_mongo", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_clip_enabled", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_audio_events", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_asr", False, raising=False)

        async def _no_label(frames, **kwargs):
            return [{} for _ in frames]

        monkeypatch.setattr(cvmod, "label_shots", _no_label)
        video = tmp_path / "v.mp4"
        self._video_with_audio(video)
        idx = asyncio.run(ci.build_index(video, force=True, mirror=False))
        assert all(not s.speech.text for s in idx.shots)
        assert all(not s.audio_events for s in idx.shots)

    def test_asr_without_backend_records_error_not_crash(self, tmp_path: Path, monkeypatch) -> None:
        """本机没有 STT 后端：必须记录原因并继续，而不是抛异常。"""
        from clipwright.services import content_audio as camod

        async def _no_stt(*a, **k):
            raise RuntimeError("whisper 未安装")

        monkeypatch.setattr("clipwright.services.stt.STTService.transcribe", _no_stt)
        rows, stats = asyncio.run(camod.align_transcript_to_shots(
            tmp_path / "ghost.mp4", [(0.0, 1.0)]))
        assert rows[0]["text"] == ""
        assert stats["phrases"] == 0
        assert stats.get("error")
