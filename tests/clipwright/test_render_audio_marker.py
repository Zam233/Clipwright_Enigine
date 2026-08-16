"""C12: 音频混合失败必须标记（不得静默静音成片）测试。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from clipwright.services.render import RenderService


@pytest.mark.asyncio
async def test_mix_audio_safe_marks_failure(tmp_path: Path) -> None:
    """无音源 → _mix_audio_safe 返回原视频 + audio_mix_failed 标记（不再静默）。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    rs = RenderService(work_dir=tmp_path / "work")

    video, marker = await rs._mix_audio_safe(str(src), [], "", "", "", "")
    assert video == str(src)
    assert marker == "audio_mix_failed"
    assert Path(video).exists()


@pytest.mark.asyncio
async def test_mix_audio_safe_exception_marked(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """异常路径 → 返回原视频 + audio_mix_error 标记 + warning 日志。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    rs = RenderService(work_dir=tmp_path / "work")

    # 传入非法音源路径强制 ffmpeg 失败路径（无音源 → 拷贝，无异常）；
    # 用 monkeypatch 让 _mix_audio 抛错验证异常标记
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    rs._mix_audio = boom  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="clipwright"):
        video, marker = await rs._mix_audio_safe(str(src), [], "", "", "", "")
    assert video == str(src)
    assert marker is not None and marker.startswith("audio_mix_error")
    assert any("音频混合失败" in rec.message for rec in caplog.records)


def test_render_result_warnings_serialized() -> None:
    """RenderResult.warnings 随 to_dict 输出。"""
    from clipwright.services.render import RenderResult
    r = RenderResult(True, warnings=["audio_mix_failed"])
    d = r.to_dict()
    assert d["warnings"] == ["audio_mix_failed"]
    r2 = RenderResult(True)
    assert r2.to_dict()["warnings"] == []
