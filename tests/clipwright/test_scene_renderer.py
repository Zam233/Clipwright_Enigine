"""场景脚本合成单测（PRD C1 / R-01 / D9）。

不依赖浏览器与 HyperFrames：只验证生成的合成 HTML 是否满足逐帧 seek 契约，
真机渲染放在门 1 的验收脚本里（``tests/clipwright/test_scene_render_live.py`` 之类，
未装 HyperFrames 时 skip）。
"""

from __future__ import annotations

from clipwright.animation.scene_renderer import (
    SCENE_CONTRACT,
    SceneSpec,
    build_composition_html,
    composition_mode,
    scene_runtime_js,
    scene_spec_json,
)
from tools.lint_determinism import scan_text

SCENE_JS = """
window.__scene = {
  duration: 5,
  renderFrame: function (t, ctx) {
    var root = document.getElementById('root');
    root.style.background = 'rgb(10,10,12)';
    root.textContent = 't=' + t.toFixed(3) + ' r=' + ctx.prng().toFixed(4);
  }
};
"""


class TestCompositionHtml:
    def test_declares_composition_contract(self) -> None:
        html = build_composition_html(SCENE_JS, duration_sec=5.0)
        assert 'data-composition-id="main"' in html
        assert 'data-duration="5.000000"' in html
        assert 'data-width="1920"' in html
        assert 'data-height="1080"' in html

    def test_registers_timeline_seek(self) -> None:
        html = build_composition_html(SCENE_JS)
        assert "window.__timelines[compId]" in html
        assert "seek: seek" in html
        assert "renderFrame(time, makeCtx(time))" in html

    def test_transparent_by_default(self) -> None:
        html = build_composition_html(SCENE_JS)
        assert "background: transparent" in html
        assert composition_mode(None)["path"] == "overlay"

    def test_opaque_background_uses_full_frame_path(self) -> None:
        html = build_composition_html(SCENE_JS, background="#0a0a0c")
        assert "background: #0a0a0c" in html
        mode = composition_mode("#0a0a0c")
        assert mode["mode"] == "opaque"
        assert mode["path"] == "full_frame"

    def test_custom_composition_id_and_size(self) -> None:
        html = build_composition_html(
            SCENE_JS, composition_id="seg_a", width=1080, height=1920, fps=30
        )
        assert 'data-composition-id="seg_a"' in html
        assert 'data-fps="30"' in html
        assert "width: 1080px" in html

    def test_invalid_arguments_rejected(self) -> None:
        for kwargs in ({"width": 0}, {"height": -1}, {"duration_sec": 0}):
            try:
                build_composition_html(SCENE_JS, **kwargs)  # type: ignore[arg-type]
            except ValueError:
                continue
            raise AssertionError(f"应当拒绝 {kwargs}")

    def test_title_is_escaped(self) -> None:
        html = build_composition_html(SCENE_JS, title='<script>"x"</script>')
        assert "<script>" not in html.split("<title>")[1].split("</title>")[0]
        assert "&lt;script&gt;" in html


class TestRuntime:
    def test_does_not_patch_globals(self) -> None:
        """2026-10-18 实测：全局替换 setTimeout/rAF 会让 HyperFrames 渲染直接失败。"""
        runtime = scene_runtime_js()
        assert "window.setTimeout =" not in runtime
        assert "window.requestAnimationFrame =" not in runtime
        assert "window.Math =" not in runtime
        # random 只挂在 __safeMath 上，不是全局 Math
        assert "__safeMath.random = function" in runtime
        assert "\nMath.random =" not in runtime

    def test_provides_shadowed_safe_handles(self) -> None:
        runtime = scene_runtime_js()
        assert "window.__deterministic" in runtime
        for handle in ("Math", "Date", "setTimeout", "setInterval", "requestAnimationFrame"):
            assert handle in runtime
        assert "不可用于场景渲染" in runtime

    def test_provides_prng(self) -> None:
        runtime = scene_runtime_js()
        assert "__prng" in runtime
        assert "prng: next" in runtime

    def test_author_script_is_wrapped_with_shadowing(self) -> None:
        html = build_composition_html(SCENE_JS)
        assert "(function (Math, Date, setTimeout, setInterval, requestAnimationFrame)" in html
        assert "window.__deterministic.Math" in html

    def test_runtime_loads_before_author_script(self) -> None:
        html = build_composition_html(SCENE_JS)
        assert html.index("window.__deterministic") < html.index("window.__scene =")

    def test_seek_tolerates_missing_scene(self) -> None:
        runtime = scene_runtime_js()
        assert "作者脚本尚未注入" in runtime
        assert "window.__hfSceneReady" in runtime

    def test_generated_html_is_lint_clean(self) -> None:
        """生成的合成不应被自己的确定性 lint 判为违规（含作者脚本与运行时）。"""
        html = build_composition_html(SCENE_JS)
        assert scan_text(html) == []


class TestSpecJson:
    def test_spec_json_is_stable(self) -> None:
        spec = SceneSpec(composition_id="a", duration_sec=5.0, background="#000000")
        assert scene_spec_json(spec) == scene_spec_json(spec)
        assert '"mode": "opaque"' in scene_spec_json(spec)

    def test_contract_documented(self) -> None:
        assert "renderFrame" in SCENE_CONTRACT and "window.__scene" in SCENE_CONTRACT
