"""_mix_audio 不得使用 -shortest — 单元回归测试。

背景: 旁白/背景乐通常短于整片时长（如 658s 旁白 vs 758s 成片）。
历史实现中两条 ffmpeg 分支都带 ``-shortest``，会把输出裁剪到较短音轨的长度，
导致最终成片被截断（660s 而非 758s）。

本测试 patch ``self._ff`` 捕获传给 ffmpeg 的完整命令列表，断言两条分支
（voice+bgm / voice-only）的命令中都不含 ``-shortest`` 元素。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from clipwright.services.render import RenderService


def _touch(path: Path, size: int = 2048) -> Path:
    """构造一个非空占位文件，满足 ``Path.exists()`` 与 ``_is_valid_video`` 的最小字节数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


def _svc(tmp_path) -> RenderService:
    return RenderService(tmp_path)


async def _run_mix_audio(svc: RenderService, *, voice: str, bgm: str = ""):
    """调用 _mix_audio，patch _ff 捕获命令；返回捕获到的所有 ffmpeg 命令列表。"""
    captured: list[list[str]] = []

    async def fake_ff(cmd, **kw):
        captured.append(cmd)
        output = cmd[-1]
        _touch(Path(output), size=4096)
        return AsyncMock()

    svc._ff = fake_ff  # type: ignore[method-assign]
    await svc._mix_audio(
        "in.mp4", [], "out.mp4",
        afp=voice, ab="192k", bfp=bgm, bitrate="5M",
    )
    return captured


class TestMixAudioNoShortest:
    @pytest.mark.asyncio
    async def test_voice_and_bgm_branch_has_no_shortest(self, tmp_path) -> None:
        """voice+bgm 分支：amix 命令不得包含 -shortest。"""
        voice = _touch(Path(tmp_path) / "voice.mp3")
        bgm = _touch(Path(tmp_path) / "bgm.mp3")
        commands = await _run_mix_audio(_svc(tmp_path), voice=str(voice), bgm=str(bgm))

        assert commands, "应至少捕获一条 ffmpeg 命令（voice+bgm 分支）"
        mix_cmd = commands[0]
        assert "-i" in mix_cmd and "amix" in " ".join(mix_cmd)
        assert "-shortest" not in mix_cmd

    @pytest.mark.asyncio
    async def test_voice_only_branch_has_no_shortest(self, tmp_path) -> None:
        """voice-only 分支：直连旁白命令不得包含 -shortest。"""
        voice = _touch(Path(tmp_path) / "voice.mp3")
        commands = await _run_mix_audio(_svc(tmp_path), voice=str(voice))

        assert commands, "应至少捕获一条 ffmpeg 命令（voice-only 分支）"
        assert "-shortest" not in commands[0]

    @pytest.mark.asyncio
    async def test_voice_and_bgm_captures_exactly_one_command(self, tmp_path) -> None:
        """voice+bgm 分支成功时只发一次 ffmpeg（不落入 voice-only 兜底）。"""
        voice = _touch(Path(tmp_path) / "voice.mp3")
        bgm = _touch(Path(tmp_path) / "bgm.mp3")
        commands = await _run_mix_audio(_svc(tmp_path), voice=str(voice), bgm=str(bgm))
        assert len(commands) == 1

    @pytest.mark.asyncio
    async def test_voice_only_captures_exactly_one_command(self, tmp_path) -> None:
        """voice-only 分支成功时只发一次 ffmpeg（不落入 copy 兜底）。"""
        voice = _touch(Path(tmp_path) / "voice.mp3")
        commands = await _run_mix_audio(_svc(tmp_path), voice=str(voice))
        assert len(commands) == 1
