"""E2E 修复：VideoTrimTool 支持 http(s) URL 输入（此前 os.path.isfile(URL)=False → 素材永远失败）。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.tool.video import VideoTrimTool


@pytest.mark.asyncio
async def test_local_path_passes_through(monkeypatch) -> None:
    """本地文件路径不触发下载，直接裁剪。"""
    tool = VideoTrimTool()
    captured = {}

    async def fake_ffmpeg(*args, **kwargs):
        captured["args"] = list(args)
        # 模拟成功裁剪
        import subprocess
        out = args[-1]
        from pathlib import Path
        Path(out).write_bytes(b"x" * 3000)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr("clipwright.tool.video._ffmpeg", fake_ffmpeg)
    # 本地存在的文件（临时写一个）
    import tempfile
    src = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    src.write(b"x" * 3000)
    src.close()

    result = await tool.execute(input_path=src.name, start_sec=0, duration_sec=2)
    # 关键断言：没有走 URL 下载（_ensure_local 对本地直接返回），-i 后是本地路径
    i = captured["args"].index("-i")
    assert captured["args"][i + 1] == src.name


@pytest.mark.asyncio
async def test_url_input_downloads_then_trims(monkeypatch) -> None:
    """URL 输入先下载到 _cache/tmp 再裁剪（E2E 核心修复）。"""
    tool = VideoTrimTool()
    dl_calls = []

    async def fake_ensure_local(url: str) -> str:
        dl_calls.append(url)
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        f.write(b"x" * 3000)
        f.close()
        return f.name

    async def fake_ffmpeg(*args, **kwargs):
        import subprocess
        out = args[-1]
        from pathlib import Path
        Path(out).write_bytes(b"x" * 3000)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(tool, "_ensure_local", fake_ensure_local)
    monkeypatch.setattr("clipwright.tool.video._ffmpeg", fake_ffmpeg)

    url = "https://videos.pexels.com/video-files/test.mp4"
    result = await tool.execute(input_path=url, start_sec=0, duration_sec=2)
    assert dl_calls == [url]  # 确实走了下载分支
    assert "missing/corrupt" not in (result.error or "")


@pytest.mark.asyncio
async def test_url_download_failure_returns_error(monkeypatch) -> None:
    """URL 下载失败 → 返回错误（不崩溃）。"""
    tool = VideoTrimTool()

    async def fake_ensure_local(url: str) -> str:
        return None  # 下载失败

    monkeypatch.setattr(tool, "_ensure_local", fake_ensure_local)
    result = await tool.execute(
        input_path="https://videos.pexels.com/video-files/none.mp4",
        start_sec=0, duration_sec=2,
    )
    assert str(result.status) == "error"
    assert "missing/corrupt" in (result.error or "")
