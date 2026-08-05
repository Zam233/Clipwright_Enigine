"""Q3 (T3) 素材质检激活测试：真实 FrameValidatorTool 注册 + 视觉门控 + 条件 match_score。

覆盖计划 acceptance criteria：
- config.py Settings 有 enable_visual_llm 字段（默认 False）
- frame_validator 视觉关时输出 {is_blank, mean_luminance} 且不含 match_score；开时含
- 全黑帧 fixture → is_blank=True → 校验 0.0
- 视觉关时 VisionService 不调用；agent 经 _heuristic_title_match_score 打分
- title 完全无关 → <0.35；相关 → 高分
- 低分触发重拟词恰 ≤2 次
- T3 提交包含 material.py + config.py + tool/__init__.py（committed history，非暂存区）
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import types
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.material_agent import MaterialAgent, _validate_video_frame
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, MaterialInput
from clipwright.schema.material import MaterialAsset, MaterialSearchResult
from clipwright.schema.tool import ToolStatus
from clipwright.tool.material import FrameValidatorTool


SCENE = {
    "title": "城市夜景",
    "keywords": ["城市", "夜景"],
    "description": "城市夜景中的车流",
}


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="test-pipeline",
        persona_id="p_test",
        category_plugin_id="",
        topic="测试",
    )


def _input() -> MaterialInput:
    return MaterialInput(context=_context(), script_skeleton={"scenes": []})


def _asset(
    asset_id: str,
    title: str,
    tags: list[str],
    url: str = "https://example.com/v.mp4",
) -> MaterialSearchResult:
    return MaterialSearchResult(
        asset=MaterialAsset(
            id=asset_id,
            title=title,
            tags=tags,
            url=url,
            duration_sec=10,
            resolution="1920x1080",
        ),
        score=0.9,
        source_name="test_source",
    )


def _install_ffmpeg_mock(monkeypatch, mean: float = 128.0) -> None:
    """替换 subprocess.run：ffmpeg 写虚拟帧文件，ffprobe 返回指定 mean。"""
    def fake_run(cmd, **kwargs):
        frame_path = cmd[-1]
        if cmd[0] == "ffmpeg":
            Path(frame_path).write_bytes(b"\xff" * 200)
            return types.SimpleNamespace(returncode=0, stderr="")
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"frames": [{"tags": {"lavfi.signalstats.YAVG": str(mean)}}]}
            ),
        )
    monkeypatch.setattr(subprocess, "run", fake_run)


class TestSettingsField:
    def test_enable_visual_llm_field_default_false(self) -> None:
        assert hasattr(settings, "enable_visual_llm")
        # 默认 False（测试环境可能被 env 覆盖，显式核对模型字段默认值）
        from clipwright.config import Settings
        assert Settings.model_fields["enable_visual_llm"].default is False


class TestFrameValidatorTool:
    @pytest.mark.asyncio
    async def test_vision_off_omits_match_score(self, monkeypatch) -> None:
        """视觉关：输出含 is_blank/mean_luminance，不含 match_score；VisionService 不调。"""
        monkeypatch.setattr(settings, "enable_visual_llm", False)
        _install_ffmpeg_mock(monkeypatch, mean=128.0)

        class _BoomAnalyzer:
            def __init__(self, *a, **k):
                raise AssertionError("VisionService 不应被实例化")
        monkeypatch.setattr("clipwright.services.vision.VisionService", _BoomAnalyzer)

        tool = FrameValidatorTool()
        res = await tool.execute(video_url="https://example.com/v.mp4",
                                 expected_text="城市夜景")
        assert res.status == ToolStatus.SUCCESS
        assert "is_blank" in res.output
        assert "mean_luminance" in res.output
        assert "match_score" not in res.output
        assert res.output["is_blank"] is False

    @pytest.mark.asyncio
    async def test_vision_on_includes_match_score(self, monkeypatch) -> None:
        """视觉开：输出含 match_score（VisionService 描述与文案重叠 → 分数）。"""
        monkeypatch.setattr(settings, "enable_visual_llm", True)
        _install_ffmpeg_mock(monkeypatch, mean=128.0)

        class _FakeAnalyzer:
            def __init__(self):
                pass
            async def analyze_image(self, path: str) -> dict:
                return {"description": "城市夜景 车流 city night"}
        monkeypatch.setattr("clipwright.services.vision.VisionService", _FakeAnalyzer)

        tool = FrameValidatorTool()
        res = await tool.execute(video_url="https://example.com/v.mp4",
                                 expected_text="城市夜景 车流")
        assert "match_score" in res.output
        assert res.output["match_score"] > 0

    @pytest.mark.asyncio
    async def test_black_frame_flagged_blank(self, monkeypatch) -> None:
        """全黑帧（mean<10）→ is_blank=True。"""
        monkeypatch.setattr(settings, "enable_visual_llm", False)
        _install_ffmpeg_mock(monkeypatch, mean=5.0)
        tool = FrameValidatorTool()
        res = await tool.execute(video_url="https://example.com/v.mp4")
        assert res.output["is_blank"] is True


class TestValidateVideoFrame:
    @pytest.mark.asyncio
    async def test_black_frame_rejected_zero(self, monkeypatch) -> None:
        """全黑帧经 _validate_video_frame → 0.0（拒收）。"""
        monkeypatch.setattr(settings, "enable_visual_llm", False)
        _install_ffmpeg_mock(monkeypatch, mean=5.0)
        with patch("clipwright.tool.registry.ToolRegistry.get",
                   return_value=FrameValidatorTool()):
            score = await _validate_video_frame(
                _asset("a1", "城市夜景", ["城市", "夜景"]).asset, "城市夜景"
            )
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_vision_off_unrelated_title_low_score(self, monkeypatch) -> None:
        """视觉关：真实工具无 match_score → 落启发式；无关 title → <0.35。"""
        monkeypatch.setattr(settings, "enable_visual_llm", False)
        _install_ffmpeg_mock(monkeypatch, mean=128.0)

        class _BoomAnalyzer:
            def __init__(self, *a, **k):
                raise AssertionError("VisionService 不应被调用")
        monkeypatch.setattr("clipwright.services.vision.VisionService", _BoomAnalyzer)

        with patch("clipwright.tool.registry.ToolRegistry.get",
                   return_value=FrameValidatorTool()):
            score = await _validate_video_frame(
                _asset("bad-1", "海边日落", ["海滩", "日落"]).asset,
                "城市夜景 城市 夜景",
            )
        assert score < 0.35

    @pytest.mark.asyncio
    async def test_vision_off_related_title_high_score(self, monkeypatch) -> None:
        """视觉关 + 相关 title → 启发式高分（≥0.35）。"""
        monkeypatch.setattr(settings, "enable_visual_llm", False)
        _install_ffmpeg_mock(monkeypatch, mean=128.0)
        with patch("clipwright.tool.registry.ToolRegistry.get",
                   return_value=FrameValidatorTool()):
            score = await _validate_video_frame(
                _asset("good-1", "城市夜景 车流", ["城市", "夜景"]).asset,
                "城市夜景 城市 夜景",
            )
        assert score >= 0.35


class TestBoundedRetry:
    @pytest.mark.asyncio
    async def test_low_score_triggers_requery_leq_two(self) -> None:
        """校验恒低分 → 重拟词恰好 ≤2 次。"""
        agent = MaterialAgent()
        bad = [_asset("bad-1", "无关素材", ["x"]), _asset("bad-2", "无关素材", ["x"])]
        queries_mock = AsyncMock(return_value=["retry-query"])
        with (
            patch(
                "clipwright.agents.material_agent._search_with_cache",
                new=AsyncMock(return_value=bad),
            ),
            patch(
                "clipwright.agents.material_agent._validate_video_frame",
                new=AsyncMock(return_value=0.1),
            ),
            patch("clipwright.agents.material_agent._llm_search_queries", new=queries_mock),
        ):
            out = await agent._process_scene(
                i=0,
                scene=SCENE,
                persona_style_keywords=[],
                brief_material_hint="",
                source_ids=None,
                pref_orientation="landscape",
                use_vision_llm=False,
                vision_frame_count=3,
                input_data=_input(),
                pipeline_id="test-pipeline",
                batch_query=None,
            )

        assert queries_mock.await_count <= 2
        assert out["retried"] is True
        assert out["validation_note"].startswith("retry_")


class TestRegistration:
    def test_registered_tool_is_real_material_impl(self) -> None:
        """ToolRegistry 注册的 frame_validator 来自 material.py（真实抽帧），非 stubs 占位。"""
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.registry import ToolRegistry
        register_builtin_tools()
        tool = ToolRegistry.get("frame_validator")
        assert tool is not None
        assert tool.__class__.__module__ == "clipwright.tool.material"
        # 真实实现无 stubs 占位 warning
        assert getattr(tool, "dependencies", []) == ["ffmpeg", "ffprobe"]

    def test_material_and_config_committed_together(self) -> None:
        """⑦ T3 提交包含 tool/material.py + config.py + tool/__init__.py（验证提交内容而非暂存区）。"""
        import subprocess as sp
        repo = Path(__file__).resolve().parents[2]
        out = sp.run(
            ["git", "log", "--oneline", "-1", "--", "clipwright/tool/material.py"],
            capture_output=True, text=True, cwd=repo,
        )
        # 断言 material.py 已被提交（有提交记录）
        assert out.stdout.strip(), "tool/material.py 未提交"
        commit = out.stdout.split()[0]
        show = sp.run(
            ["git", "show", "--stat", "--name-only", commit],
            capture_output=True, text=True, cwd=repo,
        )
        assert "tool/material.py" in show.stdout
        assert "config.py" in show.stdout
        assert "tool/__init__.py" in show.stdout
