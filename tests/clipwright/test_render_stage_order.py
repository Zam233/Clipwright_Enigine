"""渲染合成阶段顺序与切点淡化回归测试（V1：video-use 硬规则 1 / 规则 3）。

- 规则 1：文本/字幕必须在所有 overlay（图解/MG、画中画）**之后**合成，
  否则动画会盖住字幕。
- 规则 3：音频片段与静音相接的边界补 30ms 淡化，消除硬切爆音；
  同一轨上首尾相接的两段视为连续声源，不补（避免接缝下陷）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services.render import RenderService


def _svc() -> RenderService:
    return RenderService()


class TestStageOrder:
    def _run(self, svc: RenderService, calls: list[str]) -> str:
        async def _hf(video, text_overlays, hf_ov_local, width, height, fps, progress_callback=None):
            calls.append("hyperframes")
            return "v1.mp4"

        async def _pip(video, segments, width, height):
            calls.append("pip")
            return "v2.mp4"

        async def _text(video, overlays, encoder, preset, progress_callback=None,
                        width=1920, height=1080, progress_base=None):
            calls.append(f"text(base={progress_base})")
            return "v3.mp4"

        async def _available(*_a, **_kw):
            return True

        svc._apply_all_hyperframes = _hf  # type: ignore[assignment]
        svc._apply_overlays_safe = _pip  # type: ignore[assignment]
        svc._apply_text_concat = _text  # type: ignore[assignment]
        svc._hyperframes_available_async = _available  # type: ignore[assignment]

        return asyncio.run(svc._compose_video_stages(
            "v0.mp4", [{"text": "字幕"}], [{"mg_html": "<html/>"}], [{"source_path": "pip.mp4"}],
            "libx264", "medium", 1920, 1080, 30.0, None,
        ))

    def test_text_is_last(self) -> None:
        """顺序必须是 图解/MG → 画中画 → 文本/字幕。"""
        calls: list[str] = []
        out = self._run(_svc(), calls)
        assert calls == ["hyperframes", "pip", "text(base=95.0)"]
        assert out == "v3.mp4"

    def test_progress_base_keeps_monotonic(self) -> None:
        """文本最后执行时进度基准前移并封顶 97，后接音频 98 / 完成 99,100。"""
        assert RenderService._text_progress_pct(95.0, 0, 10) == pytest.approx(95.0)
        assert RenderService._text_progress_pct(95.0, 10, 10) == pytest.approx(97.0)
        # 基准超过封顶值时也不回退/不越界
        assert RenderService._text_progress_pct(99.0, 0, 10) == pytest.approx(97.0)
        # 历史语义（progress_base=None）保持不变
        assert RenderService._text_progress_pct(None, 0, 10) == pytest.approx(60.0)
        assert RenderService._text_progress_pct(None, 10, 10) == pytest.approx(70.0)


class TestDefaultCutFades:
    _FADE = RenderService._CUT_FADE_DEFAULT_SEC

    def _voice(self, start: float, dur: float, *, fade_in=None, fade_out=None) -> dict:
        return {"path": f"v{start}", "start": start, "dur": dur,
                "fade_in": fade_in, "fade_out": fade_out,
                "is_voice": True, "is_bgm": False}

    def test_isolated_clip_gets_both_fades(self) -> None:
        v = [self._voice(2.0, 3.0)]
        RenderService._apply_default_cut_fades(v)
        assert v[0]["fade_in"] == self._FADE
        assert v[0]["fade_out"] == self._FADE

    def test_contiguous_clips_only_fade_at_outer_edges(self) -> None:
        a = self._voice(3.0, 2.0)
        b = self._voice(5.0, 2.0)
        RenderService._apply_default_cut_fades([a, b])
        assert a["fade_in"] == self._FADE      # 外侧边界 → 补
        assert a["fade_out"] == 0.0            # 与 b 相接 → 不补
        assert b["fade_in"] == 0.0             # 与 a 相接 → 不补
        assert b["fade_out"] == self._FADE     # 外侧边界 → 补

    def test_explicit_zero_is_respected(self) -> None:
        v = [self._voice(0.0, 4.0, fade_in=0.0, fade_out=0.5)]
        RenderService._apply_default_cut_fades(v)
        assert v[0]["fade_in"] == 0.0
        assert v[0]["fade_out"] == 0.5

    def test_unknown_duration_gets_fade_in_only(self) -> None:
        """整文件型音源（dur=0）时长未知 → 只补淡入，不猜测结尾位置。"""
        v = [self._voice(0.0, 0.0)]
        RenderService._apply_default_cut_fades(v)
        assert v[0]["fade_in"] == self._FADE
        assert v[0]["fade_out"] == 0.0

    def test_groups_are_independent(self) -> None:
        """人声与 BGM 分属不同组：即便时间相接也各自补淡化。"""
        voice = self._voice(0.0, 5.0)
        bgm = {"path": "bgm.mp3", "start": 5.0, "dur": 5.0,
               "fade_in": None, "fade_out": None, "is_voice": False, "is_bgm": True}
        RenderService._apply_default_cut_fades([voice, bgm])
        assert voice["fade_out"] == self._FADE
        assert bgm["fade_in"] == self._FADE

    def test_none_normalized_to_number(self) -> None:
        """方法结束后不得残留 None（下游用 `> 0` 直接比较）。"""
        v = [self._voice(0.0, 1.0)]
        RenderService._apply_default_cut_fades(v)
        assert isinstance(v[0]["fade_in"], float)
        assert isinstance(v[0]["fade_out"], float)

    def test_empty_input_is_noop(self) -> None:
        RenderService._apply_default_cut_fades([])
        RenderService._apply_default_cut_fades(None)  # type: ignore[arg-type]
