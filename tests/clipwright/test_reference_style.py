"""P8: 参考成片风格模仿测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.services import style_analyzer

client = TestClient(app)


def test_analyze_fallback_no_ffmpeg(tmp_path) -> None:
    """ffmpeg 不可用 → 返回空分析（不抛异常）。"""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"not-a-video")

    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        import asyncio
        result = asyncio.run(style_analyzer.analyze_reference_video(str(video)))

    assert result["source"] == str(video)
    # 至少包含节奏/配色/转场中的一个分析结果（此处全部失败 → 仅 source）
    assert "rhythm" not in result


def test_analyze_parses_rhythm_from_ffmpeg(tmp_path) -> None:
    """模拟 ffmpeg scene 输出 → 节奏参数解析。"""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"fake")

    class R:
        returncode = 0
        stderr = (
            "frame:1 pts_time:0.0\nlavfi.scene_score=1.0\n"
            "frame:2 pts_time:2.5\nlavfi.scene_score=1.0\n"
            "frame:3 pts_time:5.0\nlavfi.scene_score=1.0\n"
        )

    with patch("subprocess.run", side_effect=[R(), R()]):
        import asyncio
        result = asyncio.run(style_analyzer.analyze_reference_video(str(video)))

    assert "rhythm" in result
    assert result["rhythm"]["shot_count"] == 2
    assert result["rhythm"]["shot_duration_mu_ms"] == 2500.0


def test_persona_reference_endpoint_404_unknown() -> None:
    resp = client.post("/api/persona/persona_nonexistent/reference-style", json={
        "video_path": "/tmp/x.mp4",
    })
    assert resp.status_code == 404
