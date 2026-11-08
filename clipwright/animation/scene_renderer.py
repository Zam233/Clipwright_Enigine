"""场景脚本合成（PRD C1 / R-01）。

把"一个导出 ``renderFrame(t)`` 的 JS 模块"包装成 HyperFrames 能逐帧 seek 的合成：

- 全帧页面：``#root[data-composition-id][data-duration][data-width][data-height]``；
- 注册 ``window.__timelines[compId] = { duration, seek(t) }``，``seek`` 调用
  ``window.__scene.renderFrame(t, ctx)``——**每帧都是 t 的纯函数**；
- 运行时冻结：``Math.random`` 走确定性 prng、``Date.now()`` 归零、
  ``setTimeout/setInterval/requestAnimationFrame`` 直接抛错（早失败好过错帧）。

背景透明度**不在本模块决定**（PRD §I.6 决策权矩阵：动画 agent 提案、主 agent 裁决）：
调用方传 ``background`` 即"该镜不透明"，传 ``None`` 即透明叠加层；这里只负责如实生成。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

#: 场景 JS 的运行契约：
#: ``window.__scene = { duration?: number, renderFrame(t: number, ctx: SceneCtx) }``
SCENE_CONTRACT = """window.__scene = {
  duration: <秒>,
  renderFrame: function (t, ctx) { /* 纯函数：只依赖 t 与 ctx */ }
};"""


@dataclass(frozen=True)
class SceneSpec:
    """一个场景合成的最小描述（供 build 与测试共用）。"""

    composition_id: str = "main"
    width: int = 1920
    height: int = 1080
    fps: float = 60.0
    duration_sec: float = 5.0
    background: Optional[str] = None  # None = 透明（叠加层路径）
    title: str = "scene"


def _prng_js(seed_expr: str = "0x9e3779b9") -> str:
    """xorshift32：给定种子序列可复现，供 ``ctx.prng`` 与 ``Math.random`` 使用。"""
    return f"""
function __xorshift32(state) {{
  var x = state >>> 0;
  x ^= (x << 13) >>> 0; x >>>= 0;
  x ^= x >>> 17;
  x ^= (x << 5) >>> 0; x >>>= 0;
  return x >>> 0;
}}
function __prng(seed) {{
  var state = (seed >>> 0) || {seed_expr};
  return function next() {{
    state = __xorshift32(state);
    return state / 4294967296;
  }};
}}
"""


def scene_runtime_js() -> str:
    """运行时片段：确定性 prng + **作用域内**墙钟冻结 + timeline 注册。

    重要（2026-10-18 实测）：**不能替换全局** ``setTimeout`` / ``requestAnimationFrame``
    ——HyperFrames 自己的播放器与逐帧捕获依赖它们，全局替换会让渲染直接失败
    （``[deterministic] requestAnimationFrame 不可用`` → Render failed）。

    正确做法是把作者脚本包进一个 IIFE，用**同名参数遮蔽** ``Math/Date/setTimeout/
    setInterval/requestAnimationFrame``：作者代码拿到的是安全替身，平台代码仍用真货。
    """

    return (
        "(function(){\n"
        + _prng_js()
        + """
var __seed = 0x2f6e2b1;
// 安全替身：作者脚本经 IIFE 遮蔽后看到的是这些，而不是真正的全局对象
var __safeMath = Object.create(Math);
__safeMath.random = function () { return __prng(__seed ^ 0x85ebca6b)(); };
var __RealDate = Date;
var __fixedMs = 0;
function __FrozenDate() {
  if (!(this instanceof __FrozenDate)) { return new __RealDate(__fixedMs).toString(); }
  return new __RealDate(__fixedMs);
}
__FrozenDate.now = function () { return __fixedMs; };
__FrozenDate.parse = __RealDate.parse;
__FrozenDate.UTC = __RealDate.UTC;
__FrozenDate.prototype = __RealDate.prototype;
function __blocked(name) {
  return function () {
    throw new Error("[deterministic] " + name + " 不可用于场景渲染，请改为由 t 推导");
  };
}
window.__deterministic = {
  Math: __safeMath,
  Date: __FrozenDate,
  setTimeout: __blocked("setTimeout"),
  setInterval: __blocked("setInterval"),
  requestAnimationFrame: __blocked("requestAnimationFrame")
};

var root = document.getElementById("root") || document.querySelector("[data-composition-id]");
var compId = (root && root.getAttribute("data-composition-id")) || "main";
var total = root ? (parseFloat(root.getAttribute("data-duration")) || 0) : 0;

function makeCtx(t) {
  var next = __prng((__seed ^ Math.floor(t * 1000)) >>> 0);
  return { t: t, prng: next, random: next };
}
function getScene() { return window.__scene || null; }
function seek(t) {
  var time = (typeof t === "number" && isFinite(t)) ? t : 0;
  var scene = getScene();
  if (!scene || typeof scene.renderFrame !== "function") { return; }  // 作者脚本尚未注入
  scene.renderFrame(time, makeCtx(time));
}
window.__timelines = window.__timelines || {};
window.__timelines[compId] = {
  duration: function () { var scene = getScene(); return total || (scene && scene.duration) || 0; },
  totalDuration: function () { var scene = getScene(); return total || (scene && scene.duration) || 0; },
  pause: function () {},
  seek: seek
};
window.__hfSceneReady = function () { try { seek(0); } catch (e) { console.error(e); } };
})();"""
    )


#: 作者脚本的遮蔽包装：参数名与作者可能用到的全局同名，因此会被静默替换为安全替身。
AUTHOR_WRAPPER_TEMPLATE = """(function (Math, Date, setTimeout, setInterval, requestAnimationFrame) {{
{scene_js}
}})(
  window.__deterministic.Math,
  window.__deterministic.Date,
  window.__deterministic.setTimeout,
  window.__deterministic.setInterval,
  window.__deterministic.requestAnimationFrame
);"""


def build_composition_html(
    scene_js: str,
    *,
    composition_id: str = "main",
    width: int = 1920,
    height: int = 1080,
    fps: float = 60.0,
    duration_sec: float = 5.0,
    background: Optional[str] = None,
    title: str = "scene",
) -> str:
    """生成可交给 HyperFrames 的全帧合成 HTML。

    ``background`` 为 None 时页面透明（叠加层路径）；给色值时不透明（全帧路径）。
    """
    if width <= 0 or height <= 0:
        raise ValueError("width/height 必须为正")
    if duration_sec <= 0:
        raise ValueError("duration_sec 必须为正")
    body_bg = background or "transparent"
    author = AUTHOR_WRAPPER_TEMPLATE.format(scene_js=scene_js)
    return f"""<!DOCTYPE html>
<html lang="zh" data-fps="{int(fps)}" data-width="{width}" data-height="{height}">
<head>
<meta charset="utf-8" />
<title>{_escape(title)}</title>
<style>
  html, body {{ margin: 0; padding: 0; width: {width}px; height: {height}px;
    overflow: hidden; background: {body_bg}; }}
  #root {{ width: {width}px; height: {height}px; position: relative; overflow: hidden;
    background: {body_bg}; }}
</style>
</head>
<body>
<div id="root" data-composition-id="{_escape(composition_id)}"
     data-duration="{float(duration_sec):.6f}"
     data-width="{width}" data-height="{height}"
     style="width:{width}px;height:{height}px;position:relative;overflow:hidden;background:{body_bg}">
</div>
<script>
{scene_runtime_js()}
</script>
<script>
{author}
window.__hfSceneReady && window.__hfSceneReady();
</script>
</body>
</html>
"""


def composition_mode(background: Optional[str]) -> dict[str, Any]:
    """把"这一镜走哪条合成路径"表达成可记录的事实（供 ``composition_mode`` 事件）。

    实现只汇报，不做决策——决策权在动画 agent 提案 + 主 agent 裁决（PRD §I.6）。
    """
    opaque = background is not None
    return {
        "mode": "opaque" if opaque else "transparent",
        "background": background,
        "path": "full_frame" if opaque else "overlay",
    }


def scene_spec_json(spec: SceneSpec) -> str:
    return json.dumps(
        {
            "composition_id": spec.composition_id,
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "duration_sec": spec.duration_sec,
            "mode": composition_mode(spec.background)["mode"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = [
    "SCENE_CONTRACT",
    "SceneSpec",
    "build_composition_html",
    "composition_mode",
    "scene_runtime_js",
    "scene_spec_json",
]
