"""Phase 3 回归测试：渲染指纹缓存 / 编码器覆盖 / xfade 并行 / 导出预设。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from clipwright.api.render import RenderSettings, _EXPORT_PRESETS, _resolve_settings
from clipwright.services.render import (
    _current_encoder,
    _current_pix_fmt,
    _delivery_extra_args,
    _timeline_render_fingerprint,
    _video_stage_fingerprint,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


def _tl(seed: str) -> Timeline:
    return Timeline(
        id=f"t_{seed}", width=1920, height=1080, fps=30, duration_sec=6,
        tracks=[Track(id="v0", name="视频", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id=f"c_{seed}", kind=ClipKind.VIDEO, asset_id=f"a_{seed}.mp4",
                 track_id="v0", start_sec=0, duration_sec=6),
        ])],
    )


def test_timeline_fingerprint_changes_with_content():
    fp1 = _timeline_render_fingerprint(_tl("a"), "out.mp4", 1920, 1080, 30, "5M", "192k", "", "", "libx264", "medium", "yuv420p")
    fp2 = _timeline_render_fingerprint(_tl("b"), "out.mp4", 1920, 1080, 30, "5M", "192k", "", "", "libx264", "medium", "yuv420p")
    assert fp1 != fp2
    # 音频路径参与指纹（音频变更 → 走 c:v copy 快路径而非无操作返回）
    fp3 = _timeline_render_fingerprint(_tl("a"), "out.mp4", 1920, 1080, 30, "5M", "192k", "voice.mp3", "", "libx264", "medium", "yuv420p")
    assert fp1 != fp3
    # 编码器/像素格式参与指纹
    fp4 = _timeline_render_fingerprint(_tl("a"), "out.mp4", 1920, 1080, 30, "5M", "192k", "", "", "prores_ks", "medium", "yuv422p10le")
    assert fp1 != fp4


def test_video_stage_fingerprint_audio_independent(tmp_path):
    """视频阶段指纹不含音频输入：文本/素材变更才失效，音频变更不影响。"""
    f1 = tmp_path / "seg1.mp4"
    f1.write_bytes(b"x" * 100)
    f1_stat = f1.stat()
    segs = [{"id": "s1", "start_sec": 0, "duration_sec": 3}]
    ov1 = [{"text": "标题A", "start_sec": 0, "duration_sec": 3}]
    ov2 = [{"text": "标题B", "start_sec": 0, "duration_sec": 3}]
    base = [str(f1)]
    fp_a = _video_stage_fingerprint(base, ov1, segs, [], 1920, 1080, 30, "5M", "libx264", "medium", "yuv420p")
    fp_b = _video_stage_fingerprint(base, ov2, segs, [], 1920, 1080, 30, "5M", "libx264", "medium", "yuv420p")
    assert fp_a != fp_b  # 文本变更 → 失效（走全量合成）
    fp_same = _video_stage_fingerprint(base, ov1, segs, [], 1920, 1080, 30, "5M", "libx264", "medium", "yuv420p")
    assert fp_a == fp_same  # 音频无关：同样输入指纹稳定
    f1.write_bytes(b"y" * 100)  # 素材变更 → 指纹变化
    fp_c = _video_stage_fingerprint(base, ov1, segs, [], 1920, 1080, 30, "5M", "libx264", "medium", "yuv420p")
    assert fp_a != fp_c


def test_delivery_extra_args():
    assert _delivery_extra_args("prores_ks") == ["-profile:v", "3"]
    assert _delivery_extra_args("libx265") == ["-x265-params", "log-level=error"]
    assert _delivery_extra_args("libx264") == []


def test_export_presets_carry_delivery_settings():
    assert _EXPORT_PRESETS["prores422hq"]["encoder"] == "prores_ks"
    assert _EXPORT_PRESETS["prores422hq"]["pix_fmt"] == "yuv422p10le"
    assert _EXPORT_PRESETS["h265_10bit"]["encoder"] == "libx265"
    assert _EXPORT_PRESETS["h265_10bit"]["pix_fmt"] == "yuv420p10le"


def test_resolve_settings_preset_not_overwritten_by_empty_defaults():
    s = RenderSettings(preset="prores422hq")
    out = _resolve_settings(s)
    assert out["encoder"] == "prores_ks"
    assert out["pix_fmt"] == "yuv422p10le"
    # 显式覆盖仍生效
    s2 = RenderSettings(preset="prores422hq", encoder="libx264", pix_fmt="yuv420p")
    out2 = _resolve_settings(s2)
    assert out2["encoder"] == "libx264"
    assert out2["pix_fmt"] == "yuv420p"


async def test_concat_xfade_parallel_tree_shape(monkeypatch):
    """xfade 分治：10 段共 9 次合并、4 轮；每轮两两并行（验证合并次数与轮数）。"""
    from clipwright.services.render import RenderService

    svc = RenderService(work_dir=Path("."))
    calls: list[tuple[str, str]] = []

    def fake_pair(left, right, tt, td, fps, bitrate, encoder, preset, out_name):
        calls.append((left, right))
        return right  # 假输出

    monkeypatch.setattr(svc, "_xfade_pair", fake_pair)
    trimmed = [f"seg_{i}.mp4" for i in range(10)]
    segments = [{"transition_in": "fade", "transition_duration_sec": 0.4}] * 10
    out = await svc._concat_xfade_parallel(trimmed, segments, 30, "5M", "libx264", "medium")
    # 9 次合并（N-1），逐轮两两并行；mock 输出文件不存在 → 走首片段兜底
    assert len(calls) == 9
    assert out == "seg_0.mp4"
    # 收敛：最终只剩一个 item（合并结果）
    assert calls[0][0] == "seg_0.mp4" and calls[0][1] == "seg_1.mp4"


def test_render_settings_fields():
    s = RenderSettings()
    assert s.encoder == "" and s.pix_fmt == ""


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
