"""MG 动画渲染器 — 将 MG JSON 定义转为 HTML/CSS/JS 动画，供 Hyperframes 渲染。

MG 动画 JSON 格式:
{
  "animation_id": "mg_title_reveal",
  "name": "标题揭示",
  "duration_sec": 3.0,
  "width": 1920, "height": 1080,
  "elements": [
    {"type": "text", "content": "{text}", "x": "center", "y": "center",
     "keyframes": [{"time": 0, "opacity": 0, "scale": 0}, ...]},
    {"type": "shape", "shape": "rect", "color": "#4f8cff", ...}
  ],
  "params": {"text": {"type": "string"}},
  "style": {"background": "...", "font_family": "..."}
}
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from clipwright.config import logger


class MGRenderer:
    """MG 动画渲染器 — MG JSON → HTML 字符串。"""

    @staticmethod
    def render(
        mg_def: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> str:
        """将 MG 动画定义渲染为完整的 HTML 页面（供 Hyperframes 渲染）。

        Args:
            mg_def: 从 JSON 加载的 MG 动画定义
            params: 用户参数，替换 {text} {value} {unit} 等占位符

        Returns:
            完整 HTML 字符串（含 <style> 和 <script>）
        """
        params = params or {}
        w = mg_def.get("width", 1920)
        h = mg_def.get("height", 1080)
        dur = mg_def.get("duration_sec", 3.0)
        bg = mg_def.get("style", {}).get("background", "transparent")
        font_family = mg_def.get("style", {}).get("font_family", "sans-serif")

        elements_html = []
        css_animations = []
        js_code = []

        for i, elem in enumerate(mg_def.get("elements", [])):
            result = MGRenderer._render_element(elem, params, i, dur)
            if result:
                elements_html.append(result["html"])
                css_animations.append(result["css"])
                js_code.append(result["js"])

        all_css = "\n".join(css_animations)
        all_js = "\n".join(js_code)

        return f"""<!DOCTYPE html>
<html data-fps="30" data-width="{w}" data-height="{h}">
<head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{w}px;height:{h}px;overflow:hidden;background:{bg};position:relative;font-family:{font_family}}}
.mg-el{{position:absolute;will-change:transform,opacity}}
.mg-shape{{border-radius:4px}}
{all_css}
</style></head><body>
<div id="root" data-composition-id="main" data-width="{w}" data-height="{h}" data-start="0" data-duration="{dur:.2f}" style="width:{w}px;height:{h}px;position:relative;overflow:hidden">
{chr(10).join(elements_html)}
</div>
<script>
(function(){{
const root=document.getElementById('root');
const dur=parseFloat(root.dataset.duration);
{all_js}
}})();
window.__timelines = window.__timelines || {{}}; window.__timelines['main'] = {{ paused: true }};
</script>
</body></html>"""

    @staticmethod
    def _render_element(
        elem: dict, params: dict, idx: int, total_dur: float,
    ) -> dict | None:
        """渲染单个元素 → {html, css, js}。"""
        elem_type = elem.get("type", "text")
        kfs = elem.get("keyframes", [])
        if not kfs:
            return None

        eid = f"mg-e{idx}"
        anim_name = f"mg_anim_{idx}"

        # 替换占位符
        def fill(val: Any) -> Any:
            if isinstance(val, str):
                for k, v in params.items():
                    val = val.replace("{" + k + "}", str(v))
            return val

        content = fill(elem.get("content", ""))

        # 位置计算
        x = elem.get("x", "center")
        y = elem.get("y", "center")
        x_off = elem.get("x_offset", 0)
        y_off = elem.get("y_offset", 0)

        if x == "center":
            left_style = "left:50%"
            xform_x = "translateX(-50%)"
        elif x == "left":
            left_style = f"left:{20 + x_off}px"
            xform_x = ""
        elif x == "right":
            left_style = f"right:{20 + x_off}px"
            xform_x = ""
        else:
            left_style = f"left:{x}px"
            xform_x = ""

        if y == "center":
            top_style = f"top:calc(50% + {y_off}px)"
            xform_y = "translateY(-50%)"
        elif y == "top":
            top_style = f"top:{20 + y_off}px"
            xform_y = ""
        elif y == "bottom":
            top_style = f"bottom:{60 - y_off}px"
            xform_y = ""
        else:
            top_style = f"top:{y}px"
            xform_y = ""

        base_transform = f"{xform_x} {xform_y}".strip()

        # 构建 CSS keyframes
        css_parts = [f"@keyframes {anim_name}{{"]
        for kf in kfs:
            pct = (kf["time"] / max(total_dur, 0.01)) * 100
            props = {k: v for k, v in kf.items() if k != "time"}
            transforms = []
            style_attrs = []

            if "scale" in props:
                s = props.pop("scale")
                transforms.append(f"scale({s})")
            if "rotate" in props:
                r = props.pop("rotate")
                transforms.append(f"rotate({r}deg)")
            if "translate_y" in props:
                ty = props.pop("translate_y")
                transforms.append(f"translateY({ty}px)")
            if "translate_x" in props:
                tx = props.pop("translate_x")
                transforms.append(f"translateX({tx}px)")
            if "width" in props:
                w = props.pop("width")
                style_attrs.append(f"width:{w}px")

            tf = f"{base_transform} {' '.join(transforms)}".strip()
            css_parts.append(
                f"  {pct:.1f}%{{"
                + (f"transform:{tf};" if tf else "")
                + "".join(f"{k}:{v};" for k, v in props.items())
                + "".join(style_attrs)
                + "}"
            )
        css_parts.append("}")
        css = "\n".join(css_parts)

        # 构建元素 HTML
        base_css = (
            f"position:absolute;{left_style};{top_style};"
            f"transform:{base_transform};"
        )

        if elem_type == "text":
            font_size = fill(elem.get("font_size", 48))
            font_color = fill(elem.get("font_color", "#ffffff"))
            font_weight = elem.get("font_weight", "normal")
            html = (
                f'<div id="{eid}" class="mg-el clip" data-start="0" data-duration="{total_dur}" '
                f'data-track-index="1" style="{base_css}'
                f'font-size:{font_size}px;color:{font_color};font-weight:{font_weight}'
                f'">{MGRenderer._esc(content)}</div>'
            )
        elif elem_type == "shape":
            shape = elem.get("shape", "rect")
            color = elem.get("color", "#4f8cff")
            sw = elem.get("stroke_width", 0)
            sw_color = elem.get("stroke_color", color)
            w_val = fill(elem.get("width", 100))
            h_val = fill(elem.get("height", 100))
            radius = "50%" if shape == "ellipse" else elem.get("border_radius", 4)
            html = (
                f'<div id="{eid}" class="mg-el mg-shape clip" data-start="0" data-duration="{total_dur}" '
                f'data-track-index="1" style="{base_css}'
                f'width:{w_val}px;height:{h_val}px;background:{color};'
                f'border-radius:{radius};'
                + (f'border:{sw}px solid {sw_color};' if sw > 0 else "")
                + '"></div>'
            )
        else:
            return None

        # JS 控制（使用 CSS 动画 + animation-delay）
        first_kf_time = min(kf["time"] for kf in kfs)
        js = (
            f"var el=document.getElementById('{eid}');"
            f"el.style.animation='{anim_name} {total_dur}s linear forwards';"
            f"el.style.animationDelay='0s';"
        )

        return {"html": html, "css": css, "js": js}

    @staticmethod
    def _esc(text: str) -> str:
        """HTML 转义。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    @staticmethod
    def load_animation(anim_id: str) -> dict | None:
        """按 animation_id 加载 MG 动画定义。

        搜索顺序:
        1. clipwright/animation/mg/templates/ (内置 llm_mg 引擎模板)
        2. plugins/llm_mg/templates/ (向后兼容, deprecated)
        3. plugins/mg_animations/animations/ (向后兼容, deprecated)
        """
        search_paths = [
            Path(__file__).resolve().parent / "mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "llm_mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "mg_animations" / "animations",
        ]
        for base_dir in search_paths:
            if not base_dir.exists():
                continue
            for f in base_dir.iterdir():
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        if data.get("animation_id") == anim_id:
                            return data
                    except Exception:
                        continue
        return None

    @staticmethod
    def list_animations() -> list[dict]:
        """列出所有可用的 MG 动画。"""
        search_paths = [
            Path(__file__).resolve().parent / "mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "llm_mg" / "templates",
            Path(__file__).resolve().parent.parent.parent / "plugins" / "mg_animations" / "animations",
        ]
        seen_ids: set[str] = set()
        anims: list[dict] = []
        for base_dir in search_paths:
            if not base_dir.exists():
                continue
            for f in sorted(base_dir.iterdir()):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        aid = data.get("animation_id", "")
                        if aid and aid not in seen_ids:
                            seen_ids.add(aid)
                            anims.append({
                                "id": aid,
                                "name": data.get("name", ""),
                                "description": data.get("description", ""),
                                "duration_sec": data.get("duration_sec", 3.0),
                                "params": list(data.get("params", {}).keys()),
                            })
                    except Exception:
                        continue
        return anims
