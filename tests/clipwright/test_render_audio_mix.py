"""C11: BGM 真实混音/LUFS — 多音源 amix + 音量/淡入淡出/延迟 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.services.render import RenderService


@pytest.mark.asyncio
async def test_mix_audio_builds_multi_source_graph(tmp_path: Path) -> None:
    """≥2 音源 → 生成 amix+loudnorm filter graph，尊重音量/淡入淡出/时间窗。"""
    video = tmp_path / "video.mp4"
    voice = tmp_path / "voice.wav"
    bgm = tmp_path / "bgm.mp3"
    for p in (video, voice, bgm):
        p.write_bytes(b"x" * 64)
    out = tmp_path / "out.mp4"

    rs = RenderService(work_dir=tmp_path / "work")
    captured: list[str] = []

    async def fake_ff(cmd, **kwargs):
        captured.append(" ".join(cmd))
        # 伪造成功输出文件
        out.write_bytes(b"mp4")
        return type("R", (), {"returncode": 0})()

    rs._ff = fake_ff  # type: ignore[method-assign]

    segments = [
        {"source_path": str(voice), "volume": 0.8, "start_sec": 0, "duration_sec": 5,
         "audio_fade_in_sec": 0.5, "audio_fade_out_sec": None},
    ]
    await rs._mix_audio(str(video), segments, str(out), afp="", bfp=str(bgm))

    assert captured, "ffmpeg 未被调用"
    cmd = captured[0]
    # 两个音源（segment voice + bgm）都被加入输入
    assert str(voice) in cmd and str(bgm) in cmd
    # filter graph 含 amix(2) 与 loudnorm
    assert "amix=inputs=2" in cmd
    assert "loudnorm=I=-16:LRA=11:TP=-1.5" in cmd
    # 音量与淡入被应用到片段链
    assert "volume=0.8" in cmd
    assert "afade=t=in:st=0:d=0.5" in cmd
    # BGM 基准音量 0.3
    assert "volume=0.3" in cmd


@pytest.mark.asyncio
async def test_mix_audio_single_source_fallback(tmp_path: Path) -> None:
    """单音源 → 回退简单混入（不构造 amix）。"""
    video = tmp_path / "video.mp4"
    voice = tmp_path / "voice.wav"
    video.write_bytes(b"x" * 64)
    voice.write_bytes(b"y" * 64)
    out = tmp_path / "out.mp4"

    rs = RenderService(work_dir=tmp_path / "work")
    captured: list[str] = []

    async def fake_ff(cmd, **kwargs):
        captured.append(" ".join(cmd))
        out.write_bytes(b"mp4")
        return type("R", (), {"returncode": 0})()

    rs._ff = fake_ff  # type: ignore[method-assign]

    await rs._mix_audio(str(video), [], str(out), afp=str(voice))

    assert captured
    cmd = captured[0]
    assert "amix" not in cmd
    assert str(voice) in cmd
