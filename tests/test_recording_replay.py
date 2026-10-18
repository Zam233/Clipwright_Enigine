"""C0：录制回放骨架（路线 C 的"可复现"替代品）。

C 放弃了「同输入→逐字节同输出」。可接受的替代只有两条：能离线重演（本模块）与
质量可被看见（golden_runs）。所以这里测的不是"能存能取"，而是三条纪律：

1. 指纹稳定：dict 顺序、浮点噪声不许产生两条不同录制；
2. 缺失必响：回放里没有的调用必须抛 RecordingMiss，绝不偷偷走真实实现；
3. 失败可留痕：录制时失败也记（含 error），回放时不许把失败当成功。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services import recording as rec


def test_fingerprint_is_stable_across_key_order():
    a = rec.fingerprint("tool", "video_trim", {"b": 2, "a": 1})
    b = rec.fingerprint("tool", "video_trim", {"a": 1, "b": 2})
    assert a == b


def test_fingerprint_absorbs_float_noise():
    a = rec.fingerprint("tool", "video_trim", {"start": 0.1 + 0.2})
    b = rec.fingerprint("tool", "video_trim", {"start": 0.3})
    assert a == b


def test_fingerprint_changes_with_args_and_kind():
    base = rec.fingerprint("tool", "video_trim", {"x": 1})
    assert base != rec.fingerprint("tool", "video_trim", {"x": 2})
    assert base != rec.fingerprint("skill", "video_trim", {"x": 1})
    assert base != rec.fingerprint("tool", "video_concat", {"x": 1})


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="未知录制类别"):
        rec.fingerprint("bogus", "x", {})


def test_record_then_replay_roundtrip(tmp_path):
    path = tmp_path / "run.jsonl"
    recorder = rec.Recorder(path)
    recorder.record("tool", "media_probe", {"path": "a.mp4"}, {"duration": 12.5})
    recorder.record("llm", "plan", {"prompt": "做一个 30 秒视频"}, "计划：3 步")
    assert recorder.count == 2

    replayer = rec.Replayer(path)
    assert len(replayer) == 2
    assert replayer.result_of("tool", "media_probe", {"path": "a.mp4"}) == {"duration": 12.5}
    assert replayer.result_of("llm", "plan", {"prompt": "做一个 30 秒视频"}) == "计划：3 步"


def test_replay_miss_raises_and_does_not_fall_through(tmp_path):
    path = tmp_path / "run.jsonl"
    rec.Recorder(path).record("tool", "media_probe", {"path": "a.mp4"}, {"duration": 1})
    replayer = rec.Replayer(path)
    with pytest.raises(rec.RecordingMiss, match="不允许偷偷"):
        replayer.result_of("tool", "media_probe", {"path": "b.mp4"})


def test_replay_of_failed_call_is_loud(tmp_path):
    path = tmp_path / "run.jsonl"
    rec.Recorder(path).record("tool", "video_trim", {"in": "a.mp4"}, None, error="ffmpeg 失败")
    replayer = rec.Replayer(path)
    with pytest.raises(rec.RecordingError, match="失败的"):
        replayer.result_of("tool", "video_trim", {"in": "a.mp4"})


def test_corrupt_recording_is_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(rec.RecordingError, match="不是合法 JSON"):
        rec.Replayer(path)


def test_missing_field_is_rejected(tmp_path):
    path = tmp_path / "bad2.jsonl"
    path.write_text(json.dumps({"kind": "tool", "args": {}}) + "\n", encoding="utf-8")
    with pytest.raises(rec.RecordingError, match="缺少字段 name"):
        rec.Replayer(path)


def test_summarize_reports_capabilities(tmp_path):
    path = tmp_path / "run.jsonl"
    recorder = rec.Recorder(path)
    recorder.record("tool", "media_probe", {"p": 1}, {})
    recorder.record("tool", "scene_detect", {"p": 1}, {})
    recorder.record("llm", "plan", {"p": 1}, "ok")
    stat = rec.summarize(path)
    assert stat["calls"] == 3
    assert stat["by_kind"] == {"tool": 2, "llm": 1}
    assert stat["capabilities"] == ["media_probe", "plan", "scene_detect"]


def test_merge_recordings_dedupes_by_key(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    rec.Recorder(a).record("tool", "media_probe", {"p": 1}, "A")
    rec.Recorder(b).record("tool", "media_probe", {"p": 1}, "A-dup")
    rec.Recorder(b).record("tool", "scene_detect", {"p": 1}, "B")
    out = tmp_path / "merged.jsonl"
    written = rec.merge_recordings([a, b], out)
    assert written == 2
    merged = rec.Replayer(out)
    assert merged.result_of("tool", "media_probe", {"p": 1}) == "A"  # 先到先得
    assert merged.result_of("tool", "scene_detect", {"p": 1}) == "B"


def test_allow_real_mode_still_refuses_silently(monkeypatch, tmp_path):
    """即使将来打开 allow_real，也不许"悄悄"跑真实实现——当前实现必须显式报错。"""
    replayer = rec.Replayer(tmp_path / "empty.jsonl", allow_real=True)
    with pytest.raises(rec.RecordingMiss, match="allow_real"):
        replayer.result_of("tool", "media_probe", {"p": 1})
