"""QualityAgent 帧级素材匹配检查集成测试（视觉 LLM 门控）。

背景：QualityAgent 原本只做规则检查（时长/轨道/节奏/动画/转场/音量），
从不验证实际渲染素材帧与场景文案是否匹配。本测试覆盖新增的帧级检查：

- Test A：真实 ffmpeg 抽帧 —— 真实 fixture 视频 → extract_frames 产出帧文件
  （不 mock 抽帧，验证 FFmpeg 路径真实可用）
- Test B：gate 关闭（enable_visual_llm=False）→ 返回 [] 且 VisionService 不被调用
- Test C：gate 开启 + 视觉结果匹配文案 → 不产出 material_match 问题
- Test D：gate 开启 + 视觉结果不匹配文案 → 产出 material_match 错误问题
- Test E：execute() 层面 → material_match 错误触发 redo_agent="material"

注意：不依赖真实视觉 LLM（CI 无 API key）——视觉调用全部 mock，
仅抽帧路径（Test A）走真实 ffmpeg。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.quality_agent import QualityAgent
from clipwright.schema.agent import AgentContext, AgentDecision, QualityInput
from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.tool.frame_extractor import extract_frames

# ── Helpers ──


def _make_ffmpeg_video(path: Path) -> None:
    """用真实 ffmpeg 生成 1s 蓝色色块 MP4（与 test_render_progress 一致）。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=320x240:d=1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="quality_frame_check_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
    )


def _timeline(clip_text: str = "城市夜景") -> Timeline:
    """含 1 个带文案视频 clip + 1 个音频 clip 的最小时间线。"""
    video = Clip(
        id="v1", kind="video", asset_id="/tmp/fixture.mp4", track_id="t0",
        start_sec=0, duration_sec=12.0,
        metadata={"description": clip_text},
    )
    audio = Clip(
        id="a1", kind="audio", asset_id="", track_id="t1",
        start_sec=0, duration_sec=12.0, volume=1.0,
    )
    return Timeline(
        id="tl_frame_check", width=320, height=240, fps=10, duration_sec=12.0,
        tracks=[
            Track(id="t0", kind="video", index=0, clips=[video]),
            Track(id="t1", kind="audio", index=1, clips=[audio]),
        ],
    )


# ── Test A：真实 ffmpeg 抽帧 ──


@pytest.mark.asyncio
async def test_extract_frames_real_ffmpeg(tmp_path: Path) -> None:
    video = tmp_path / "fixture.mp4"
    _make_ffmpeg_video(video)
    assert video.exists() and video.stat().st_size > 0

    frames = await extract_frames(
        {"local_path": str(video), "duration_sec": 1.0},
        frame_count=1,
        temp_dir=str(tmp_path),
    )

    assert len(frames) == 1
    frame = Path(frames[0])
    assert frame.exists()
    assert frame.stat().st_size > 0


# ── Test B：gate 关闭 ──


@pytest.mark.asyncio
async def test_check_frame_matches_gate_off() -> None:
    agent = QualityAgent()

    with (
        patch("clipwright.tool.frame_extractor.extract_frames", new=AsyncMock()) as extract,
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(),
        ) as analyze,
    ):
        issues = await agent._check_frame_matches(
            _timeline(), _context(), {"enable_visual_llm": False}, enabled=False
        )

    assert issues == []
    extract.assert_not_awaited()
    analyze.assert_not_awaited()


# ── Test C：gate 开启 + 匹配 ──


@pytest.mark.asyncio
async def test_check_frame_matches_match_no_issue(tmp_path: Path) -> None:
    agent = QualityAgent()
    fake_frame = str(tmp_path / "frame.jpg")

    with (
        patch(
            "clipwright.tool.frame_extractor.extract_frames",
            new=AsyncMock(return_value=[fake_frame]),
        ),
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(return_value={
                "tags": ["城市", "夜景"],
                "description": "城市夜景车流",
                "labels": ["city"],
            }),
        ),
    ):
        issues = await agent._check_frame_matches(
            _timeline(), _context(), {"enable_visual_llm": True}, enabled=True
        )

    assert issues == []


# ── Test C2：enabled=False 强制跳过（即使 constraints 开启了开关）──


@pytest.mark.asyncio
async def test_check_frame_matches_enabled_flag_wins(tmp_path: Path) -> None:
    """C3: enabled 参数优先于 constraints 开关 — basic 深度下视觉路径不执行。"""
    agent = QualityAgent()

    with (
        patch("clipwright.tool.frame_extractor.extract_frames", new=AsyncMock()) as extract,
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(),
        ) as analyze,
    ):
        issues = await agent._check_frame_matches(
            _timeline(), _context(), {"enable_visual_llm": True}, enabled=False
        )

    assert issues == []
    extract.assert_not_awaited()
    analyze.assert_not_awaited()


# ── Test D：gate 开启 + 不匹配 ──


@pytest.mark.asyncio
async def test_check_frame_matches_mismatch_produces_issue(tmp_path: Path) -> None:
    agent = QualityAgent()
    fake_frame = str(tmp_path / "frame.jpg")

    with (
        patch(
            "clipwright.tool.frame_extractor.extract_frames",
            new=AsyncMock(return_value=[fake_frame]),
        ),
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(return_value={
                "tags": ["大海", "沙滩"],
                "description": "海边沙滩",
                "labels": ["beach"],
            }),
        ),
    ):
        issues = await agent._check_frame_matches(
            _timeline(), _context(), {"enable_visual_llm": True}, enabled=True
        )

    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].category == "material_match"
    assert "clip=v1" in issues[0].message


# ── Test E：execute() 层面 → redo_agent="material" ──


@pytest.mark.asyncio
async def test_execute_redo_agent_material_on_mismatch(tmp_path: Path) -> None:
    agent = QualityAgent()
    fake_frame = str(tmp_path / "frame.jpg")
    input_data = QualityInput(
        context=_context(),
        timeline=_timeline(),
        constraints={"enable_visual_llm": True},
    )

    with (
        patch(
            "clipwright.tool.frame_extractor.extract_frames",
            new=AsyncMock(return_value=[fake_frame]),
        ),
        patch(
            "clipwright.services.vision.VisionService.analyze_image",
            new=AsyncMock(return_value={
                "tags": ["大海", "沙滩"],
                "description": "海边沙滩",
                "labels": ["beach"],
            }),
        ),
    ):
        output = await agent.execute(input_data, _context())

    assert output.decision == AgentDecision.FAIL
    assert output.passed is False
    assert output.redo_agent == "material"
    assert any(
        i.category == "material_match" and i.severity == "error"
        for i in output.issues
    )
