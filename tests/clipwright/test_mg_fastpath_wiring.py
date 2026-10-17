"""V3 接线：`RenderService._render_mg_mov` 的快路径优先 + 失败回退语义。

快路径是**优化**，因此这里断言的重点不是"它一定生效"，而是：
- 可用时优先用它，且**不再启动 Chrome**；
- 不可用时（无 mg_def / 白名单拒绝 / 开关关闭 / 快路径抛错或产出无效文件）
  一律回退 Chrome，**失败语义与接线前完全一致**；
- `_extract_*` 必须把 `mg_def`/`mg_params` 带进 overlay 列表（否则判定无从下手）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.render import RenderService, _mg_fastpath_enabled


SAFE_DEF = {
    "animation_id": "mg_test",
    "duration_sec": 1.0,
    "width": 640,
    "height": 360,
    "elements": [
        {"type": "text", "content": "hi", "x": "center", "y": "center",
         "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
    ],
}

UNSAFE_DEF = {
    "animation_id": "mg_bad",
    "duration_sec": 1.0,
    "elements": [
        {"type": "text", "content": "hi", "x": "center", "y": "center",
         "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
        {"type": "image", "src": "x.png", "keyframes": [{"time": 0, "opacity": 1}]},
    ],
}


def _service(tmp_path: Path) -> RenderService:
    svc = RenderService.__new__(RenderService)
    svc._work_dir = tmp_path
    return svc


class TestSwitch:
    def test_default_on(self, monkeypatch) -> None:
        monkeypatch.delenv("CLIPWRIGHT_MG_FASTPATH", raising=False)
        assert _mg_fastpath_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
    def test_off_values(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", value)
        assert _mg_fastpath_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "", "anything"])
    def test_on_values(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", value)
        assert _mg_fastpath_enabled() is True


class _ChromeSpy:
    """替身：记录 Chrome 路径是否被调用。"""

    def __init__(self, result: str | None = "chrome.mov"):
        self.calls = 0
        self.result = result

    async def __call__(self, html, out_dir, mov_path, **kwargs):
        self.calls += 1
        return self.result


class TestFastpathUsed:
    def test_fastpath_hit_skips_chrome(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", "1")

        def fake_render(mg_def, out_mov, frame_size, fps, **kwargs):
            Path(out_mov).write_bytes(b"\x00" * 4096)
            return str(out_mov)

        import clipwright.animation.mg_fastpath as fp
        monkeypatch.setattr(fp, "render_mg_fastpath", fake_render)
        monkeypatch.setattr(fp, "fastpath_eligible", lambda d, p=None: (True, ""))

        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: True)

        out = asyncio.run(svc._render_mg_mov(
            {"mg_html": "<html></html>", "mg_def": SAFE_DEF, "mg_params": {"text": "x"}},
            640, 360, 30.0))
        assert out and chrome.calls == 0, "快路径命中时不应启动 Chrome"

    def test_ineligible_falls_back_to_chrome(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", "1")
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: True)

        out = asyncio.run(svc._render_mg_mov(
            {"mg_html": "<html></html>", "mg_def": UNSAFE_DEF}, 640, 360, 30.0))
        assert out == "chrome.mov" and chrome.calls == 1

    def test_no_mg_def_falls_back(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: True)

        out = asyncio.run(svc._render_mg_mov({"mg_html": "<html></html>"}, 640, 360, 30.0))
        assert out == "chrome.mov" and chrome.calls == 1

    def test_switch_off_never_tries_fastpath(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", "0")
        called = {"n": 0}

        def fake_render(*a, **k):
            called["n"] += 1
            return None

        import clipwright.animation.mg_fastpath as fp
        monkeypatch.setattr(fp, "render_mg_fastpath", fake_render)
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: True)

        out = asyncio.run(svc._render_mg_mov(
            {"mg_html": "<html></html>", "mg_def": SAFE_DEF}, 640, 360, 30.0))
        assert out == "chrome.mov" and called["n"] == 0 and chrome.calls == 1

    def test_fastpath_exception_falls_back(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", "1")

        def boom(*a, **k):
            raise RuntimeError("PIL 崩了")

        import clipwright.animation.mg_fastpath as fp
        monkeypatch.setattr(fp, "render_mg_fastpath", boom)
        monkeypatch.setattr(fp, "fastpath_eligible", lambda d, p=None: (True, ""))
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: True)

        out = asyncio.run(svc._render_mg_mov(
            {"mg_html": "<html></html>", "mg_def": SAFE_DEF}, 640, 360, 30.0))
        assert out == "chrome.mov" and chrome.calls == 1

    def test_fastpath_invalid_output_falls_back(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        monkeypatch.setenv("CLIPWRIGHT_MG_FASTPATH", "1")

        def fake_render(mg_def, out_mov, frame_size, fps, **kwargs):
            Path(out_mov).write_bytes(b"junk")
            return str(out_mov)

        import clipwright.animation.mg_fastpath as fp
        monkeypatch.setattr(fp, "render_mg_fastpath", fake_render)
        monkeypatch.setattr(fp, "fastpath_eligible", lambda d, p=None: (True, ""))
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        import clipwright.services.render as render_mod
        # 快路径产出的文件被判为无效 → 必须回退（回退后 Chrome 产物同样判无效 → None）
        monkeypatch.setattr(render_mod, "_is_valid_video", lambda p, **k: False)

        out = asyncio.run(svc._render_mg_mov(
            {"mg_html": "<html></html>", "mg_def": SAFE_DEF}, 640, 360, 30.0))
        assert out is None and chrome.calls == 1, "快路径产物无效时必须试过 Chrome"

    def test_empty_html_returns_none_without_any_render(self, tmp_path, monkeypatch) -> None:
        svc = _service(tmp_path)
        chrome = _ChromeSpy()
        import clipwright.animation.hyperframes_renderer as hr
        monkeypatch.setattr(hr.HyperframesRenderer, "render_mg_html_to_mov",
                            staticmethod(chrome))
        assert asyncio.run(svc._render_mg_mov({"mg_def": SAFE_DEF}, 640, 360, 30.0)) is None
        assert chrome.calls == 0


class TestOverlayExtractionCarriesMgDef:
    def _timeline(self):
        return Timeline(
            id="t", width=1920, height=1080, fps=30, duration_sec=6.0,
            tracks=[
                Track(id="v", name="主", kind=ClipKind.VIDEO, index=0, clips=[
                    Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a.mp4", track_id="v",
                         start_sec=0.0, duration_sec=6.0),
                ]),
                Track(id="anim", name="动画", kind=ClipKind.ANIMATION, index=1, clips=[
                    Clip(id="mg1", kind=ClipKind.ANIMATION, asset_id="", track_id="anim",
                         start_sec=1.0, duration_sec=2.0, text="标题",
                         metadata={"renderer": "mg_hyperframes", "mg_html": "<html>x</html>",
                                   "mg_def": SAFE_DEF, "mg_params": {"text": "标题"},
                                   "anim_type": "mg_test"}),
                ]),
            ],
        )

    def test_mg_def_and_params_reach_overlay_list(self, tmp_path) -> None:
        svc = _service(tmp_path)
        _v, _o, _t, _a, hf = svc._extract_segments(self._timeline())
        assert len(hf) == 1
        entry = hf[0]
        assert entry["mg_def"] == SAFE_DEF
        assert entry["mg_params"] == {"text": "标题"}
        assert entry["anim_type"] == "mg_test"
        assert entry["start_sec"] == 1.0 and entry["duration_sec"] == 2.0

    def test_overlay_entry_is_fastpath_eligible_end_to_end(self, tmp_path) -> None:
        """接线正确性：提取出来的 entry 直接喂给白名单判定必须是"可用"。"""
        from clipwright.animation.mg_fastpath import fastpath_eligible

        svc = _service(tmp_path)
        _v, _o, _t, _a, hf = svc._extract_segments(self._timeline())
        ok, why = fastpath_eligible(hf[0]["mg_def"], hf[0]["mg_params"])
        assert ok, why
