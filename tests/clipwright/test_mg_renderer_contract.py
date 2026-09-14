"""MGRenderer 输出 HyperFrames 契约合规性测试。

HyperFrames 0.7.88 要求：
- 根元素携带 data-composition-id（compositions.md:7-13）
- 受控时序元素带 class="clip" + data-start/data-duration/data-track-index
  （data-attributes.md:19-26）
- 注册 window.__timelines（gsap.md:5-26）
"""

from __future__ import annotations

from clipwright.animation.hyperframes_renderer import _fmt_sec
from clipwright.animation.mg_renderer import MGRenderer

MG_CALLOUT_BADGE: dict = {
    "animation_id": "mg_callout_badge",
    "name": "标注徽章",
    "duration_sec": 2.0,
    "width": 1920,
    "height": 1080,
    "elements": [
        {
            "type": "shape",
            "shape": "ellipse",
            "color": "{accent}",
            "x": "center",
            "y": "center",
            "y_offset": -80,
            "width": 16,
            "height": 16,
            "keyframes": [
                {"time": 0, "opacity": 0, "scale": 0},
                {"time": 0.3, "opacity": 1, "scale": 1.3},
                {"time": 0.5, "opacity": 1, "scale": 1.0},
            ],
        },
        {
            "type": "text",
            "content": "{text}",
            "x": "center",
            "y": "center",
            "y_offset": -80,
            "x_offset": 20,
            "font_size": 40,
            "font_color": "#ffffff",
            "font_weight": "bold",
            "keyframes": [
                {"time": 0.1, "opacity": 0, "translate_x": -20},
                {"time": 0.4, "opacity": 1, "translate_x": 0},
            ],
        },
    ],
    "params": {
        "text": {"type": "string", "default": "核心指标"},
        "accent": {"type": "string", "default": "#fbbf24"},
    },
    "style": {"background": "transparent", "font_family": "sans-serif"},
}


class TestMGRendererContract:
    """MGRenderer.render 输出契约测试。"""

    def test_root_div_contract(self) -> None:
        """根 div 应携带 data-composition-id / data-width / data-height / data-start。"""
        html = MGRenderer.render(MG_CALLOUT_BADGE, {"text": "测试", "accent": "#fbbf24"})
        assert 'id="root"' in html
        assert 'data-composition-id="main"' in html
        assert 'data-width="1920"' in html
        assert 'data-height="1080"' in html
        assert 'data-start="0"' in html
        assert 'data-duration="2.00"' in html

    def test_elements_have_clip_class_and_timing_attrs(self) -> None:
        """每个 .mg-el 元素都应带 class="clip" 与 data-start/data-duration/data-track-index。"""
        html = MGRenderer.render(MG_CALLOUT_BADGE, {"text": "测试", "accent": "#fbbf24"})
        assert 'class="mg-el clip"' in html
        assert 'class="mg-el mg-shape clip"' in html
        # 每个 mg-el 元素都带时序属性
        for token in ('class="mg-el clip"', 'class="mg-el mg-shape clip"'):
            idx = html.index(token)
            seg = html[idx:idx + 300]
            assert 'data-start="0"' in seg
            assert 'data-duration="2.0"' in seg
            assert 'data-track-index="1"' in seg

    def test_window_timelines_registered(self) -> None:
        """脚本末尾应注册真实 RuntimeTimelineLike（不再是空对象桩）。"""
        html = MGRenderer.render(MG_CALLOUT_BADGE, {"text": "测试", "accent": "#fbbf24"})
        assert "window.__timelines" in html
        # 审计 HFR-2：旧的 `= { paused: true }` 空对象桩既不满足上游 GSAP 适配器契约
        # （需要 duration/seek），也让 lint 静态规则判为"没有注册"。
        assert "window.__timelines['main'] = { paused: true };" not in html
        assert "window.__timelines[compId]=" in html
        assert "duration:function(){return total;}" in html
        assert "seek:seek" in html
        # 时间轴需在元素动画声明之后建立，才能收集到 WAAPI 句柄
        assert html.index("el.style.animation") < html.index("window.__timelines[compId]=")

    def test_inline_style_preserved(self) -> None:
        """根 div 原有 style 应保留。"""
        html = MGRenderer.render(MG_CALLOUT_BADGE, {"text": "测试", "accent": "#fbbf24"})
        assert 'style="width:1920px;height:1080px;position:relative;overflow:hidden"' in html


MG_BG_ONLY: dict = {
    "animation_id": "mg_bg",
    "duration_sec": 2.0,
    "width": 1920,
    "height": 1080,
    "elements": [
        {
            "type": "bg",
            "background": "#123456",
            "keyframes": [{"time": 0, "opacity": 0}, {"time": 0.5, "opacity": 1}],
        },
    ],
}


class TestBgFullBleed:
    """V3 验证时发现的真实缺陷：`bg` 全幅底层被套上 center 锚点的 translate(-50%,-50%)。

    bg 是 `inset:0` 的全幅层，**没有** center 锚点。旧实现把 base_transform 一并写进
    关键帧，于是背景层被平移自身尺寸的一半 → 只剩**左上四分之一**可见
    （1920×1080 只剩 960×540；实测 Chrome 产物的 alpha 覆盖恰好 25%）。
    """

    def test_bg_keyframes_have_no_anchor_translate(self) -> None:
        html = MGRenderer.render(MG_BG_ONLY, {}, width=1920, height=1080, fps=30)
        # 取 bg 元素那段 @keyframes（第一个）
        block = html.split("@keyframes mg_anim_0{", 1)[1].split("}", 1)[0]
        assert "translateX(-50%)" not in block
        assert "translateY(-50%)" not in block
        assert "transform" not in block
        assert "opacity" in block  # 动画本身仍然保留

    def test_bg_uses_inset_full_bleed(self) -> None:
        html = MGRenderer.render(MG_BG_ONLY, {}, width=1920, height=1080, fps=30)
        assert "position:absolute;inset:0;z-index:0;" in html

    def test_centered_elements_keep_anchor_translate(self) -> None:
        """修复不能误伤：center 锚点的文本/形状仍必须带 -50% 位移（否则不再居中）。"""
        html = MGRenderer.render(MG_CALLOUT_BADGE, {"text": "测试", "accent": "#fbbf24"})
        assert "translateX(-50%) translateY(-50%)" in html

    def test_bg_with_scale_keyframe_would_be_ineligible_in_fastpath(self) -> None:
        """带变换关键帧的 bg 在 PIL 快路径必须被判为不适用（两路径语义保持一致）。"""
        from clipwright.animation.mg_fastpath import fastpath_eligible

        mg = dict(MG_BG_ONLY)
        mg["elements"] = [{
            "type": "bg", "background": "#123456",
            "keyframes": [{"time": 0, "opacity": 1, "scale": 1.2}],
        }]
        ok, why = fastpath_eligible(mg, {})
        assert not ok and "bg" in why


class TestFmtSec:
    """_fmt_sec 秒数格式化辅助函数测试。"""

    def test_integer(self) -> None:
        assert _fmt_sec(0) == "0"
        assert _fmt_sec(2.0) == "2"

    def test_fraction(self) -> None:
        assert _fmt_sec(2.5) == "2.5"
        assert _fmt_sec(1.20) == "1.2"

    def test_sum_no_trailing_zeros(self) -> None:
        assert _fmt_sec(0.1 + 0.2) == "0.3"
