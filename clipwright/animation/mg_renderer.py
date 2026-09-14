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
import re
from pathlib import Path
from typing import Any

from clipwright.animation.hf_timeline import timeline_script
from clipwright.config import logger


class MGRenderer:
    """MG 动画渲染器 — MG JSON → HTML 字符串。"""

    # 命名曲线 → CSS animation-timing-function 映射
    # elastic/bounce 无标准 CSS 关键字，用近似 cubic-bezier
    EASING_MAP: dict[str, str] = {
        "linear": "linear",
        "ease": "ease",
        "ease-in": "ease-in",
        "ease-out": "ease-out",
        "ease-in-out": "ease-in-out",
        "back-out": "cubic-bezier(0.175, 0.885, 0.32, 1.275)",
        "elastic-out": "cubic-bezier(0.68, -0.55, 0.265, 1.55)",
        "bounce": "cubic-bezier(0.25, 0.46, 0.45, 0.94)",
    }

    # 关键帧属性 → CSS 属性名（蛇形命名转连字符）
    KEYFRAME_CSS_MAP: dict[str, str] = {
        "font_size": "font-size",
        "font_weight": "font-weight",
        "font_family": "font-family",
        "line_height": "line-height",
        "letter_spacing": "letter-spacing",
        "text_shadow": "text-shadow",
        "box_shadow": "box-shadow",
        "border_radius": "border-radius",
        "border_width": "border-width",
        "border_color": "border-color",
        "transform_origin": "transform-origin",
    }

    # 元素级已显式渲染/结构性字段，静态透传时排除，避免重复输出
    STATIC_EXCLUDE_KEYS = {
        "type", "content", "shape", "color", "background", "src",
        "x", "y", "x_offset", "y_offset", "keyframes",
        "font_size", "font_color", "font_weight",
        "width", "height", "border_radius",
        "stroke_width", "stroke_color", "border_width", "border_color",
    }

    # CSS 值中的 url(...) 会触发 headless Chrome 主动请求（SSRF /
    # 本地文件读取），LLM 生成的 MG JSON 不可信 → 一律中和。
    _URL_CSS_RE = re.compile(r"url\s*\(", re.IGNORECASE)

    @staticmethod
    def _sanitize_css_value(value: Any) -> Any:
        """D12：中和含 url(...) 的 CSS 值（返回 none）。"""
        if isinstance(value, str) and MGRenderer._URL_CSS_RE.search(value):
            logger.warning("MG CSS 值含 url()，已中和: %.80s", value)
            return "none"
        return value

    @staticmethod
    def _safe_image_src(src: str) -> str:
        """D12：图片源白名单——防 SSRF 与越权本地文件读取。

        - ``data:image/*``：允许（内联，无外部请求）
        - ``http(s)://`` / 协议相对 ``//``：拒绝（Chrome 会主动请求 → SSRF）
        - ``file://`` / 绝对路径：必须落在媒体白名单目录内且文件存在
        - 相对路径：拒绝 ``..`` 穿越；存在则转 file:// URI，否则原样保留
          （由 Chrome 相对 HTML 文档解析，不产生外部请求）

        返回空串表示拒绝渲染该元素。
        """
        s = (src or "").strip()
        if not s:
            return ""
        low = s.lower()
        if low.startswith("data:image/"):
            return s
        if low.startswith(("http://", "https://", "//")):
            return ""
        if low.startswith("file://"):
            raw = s[7:]
            # file:///C:/... → C:/...；file://host/path 不接受 host 形式
            if raw.startswith("/") and re.match(r"^/[A-Za-z]:", raw):
                raw = raw[1:]
            p = Path(raw)
        else:
            p = Path(s)
        try:
            if p.is_absolute():
                from clipwright.security import assert_allowed_path
                rp = assert_allowed_path(p.resolve())
                return rp.as_uri() if rp.exists() else ""
            if ".." in p.parts:
                return ""
            if p.exists():
                return p.resolve().as_uri()
            return s
        except Exception:
            return ""

    @staticmethod
    def _css_timing(easing: Any) -> str:
        """将 easing 字段转换为 CSS animation-timing-function 值。

        - 命名曲线 → EASING_MAP
        - [x1, y1, x2, y2] 数组 → cubic-bezier(x1, y1, x2, y2) 透传
        - 未知值回退到 ease（容错）
        """
        if isinstance(easing, (list, tuple)) and len(easing) == 4:
            try:
                nums = ", ".join(str(float(n)) for n in easing)
                return f"cubic-bezier({nums})"
            except (TypeError, ValueError):
                # 审计 P3 修复：非数值 bezier 数组回退 ease，避免整元素渲染失败
                return "ease"
        name = str(easing)
        if name.startswith("cubic-bezier(") and name.endswith(")"):
            return name
        return MGRenderer.EASING_MAP.get(name, "ease")

    # ── 变量参数化（P1-5：上游 data-composition-variables / --variables） ──

    #: 颜色类参数名 hint（用于推断上游变量 type=color）
    _COLOR_HINTS = ("color", "colour", "accent", "primary", "secondary", "bg", "background")
    _COLOR_VALUE_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$|^rgba?\(")

    @staticmethod
    def _json_attr(payload: Any) -> str:
        """把对象序列化为可安全嵌入 HTML 属性的 JSON（双引号属性 + &quot; 转义）。"""
        import json as _json
        return _json.dumps(payload, ensure_ascii=False).replace("&", "&amp;").replace('"', "&quot;")

    @classmethod
    def _infer_var_type(cls, pid: str, spec: Any, resolved: Any) -> str:
        """推断上游变量类型：color / number / string（上游只认这几种标量）。"""
        declared = ""
        default: Any = resolved
        if isinstance(spec, dict):
            declared = str(spec.get("type", "") or "").lower()
            if spec.get("default") is not None:
                default = spec.get("default")
        text = str(default if default not in (None, "") else resolved or "")
        if declared == "number":
            return "number"
        try:
            float(text)
            return "number"
        except (TypeError, ValueError):
            pass
        low = pid.lower()
        if any(h in low for h in cls._COLOR_HINTS) or cls._COLOR_VALUE_RE.match(text):
            return "color"
        return "string"

    @classmethod
    def build_variables(cls, mg_def: dict[str, Any], params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """生成 `data-composition-variables` 声明数组（id/type/label/default）。

        取值优先级：调用方传入的 params（本次渲染的真实值）→ 模板声明的 default → 空串。
        这样"不传 --variables 也能正确渲染"（上游用声明里的 default），
        同时 Studio/批量渲染/`--variables` 覆盖都有据可依。
        """
        params = params or {}
        declared = mg_def.get("params") or {}
        ids: list[str] = [str(k) for k in declared]
        # 模板未声明但元素里用到的占位符也要声明，否则会被判为未声明变量
        for m in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", json.dumps(mg_def, ensure_ascii=False)):
            if m not in ids:
                ids.append(m)
        out: list[dict[str, Any]] = []
        for pid in ids:
            spec = declared.get(pid)
            resolved = params.get(pid)
            if resolved is None:
                resolved = spec.get("default", "") if isinstance(spec, dict) else ""
            out.append({
                "id": pid,
                "type": cls._infer_var_type(pid, spec, resolved),
                "label": pid,
                "default": "" if resolved is None else str(resolved),
            })
        return out

    @staticmethod
    def _bind_or_fill(raw: Any, params: dict[str, Any], bound_ids: set[str]) -> Any:
        """标量占位符 → CSS 变量引用（带内联兜底），其余照旧内联替换。

        - 整个值就是一个 `{key}` 且 key 已声明为变量 → `var(--key, <解析值>)`：
          上游会把每个标量变量自动挂成 `--key` 自定义属性，因此 `--variables` 覆盖生效；
          未传变量时 `var()` 的兜底值保证渲染结果与旧行为一致。
        - 占位符嵌在更长的字符串里（如 `2px solid {accent}`）→ 只能内联替换。
        """
        if not isinstance(raw, str):
            return raw
        m = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", raw.strip())
        if m and m.group(1) in bound_ids:
            key = m.group(1)
            resolved = str(params.get(key, "") or "")
            return f"var(--{key}, {resolved})" if resolved else f"var(--{key})"
        out = raw
        for k, v in params.items():
            out = out.replace("{" + k + "}", str(v))
        return out

    @staticmethod
    def render(
        mg_def: dict[str, Any],
        params: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
    ) -> str:
        """将 MG 动画定义渲染为完整的 HTML 页面（供 Hyperframes 渲染）。

        Args:
            mg_def: 从 JSON 加载的 MG 动画定义
            params: 用户参数，替换 {text} {value} {unit} 等占位符；
                    同时声明为上游 HyperFrames 变量（data-composition-variables），
                    文本/标量可被 --variables 覆盖
            width: 拟定分辨率宽度（调用方时间线尺寸优先，缺省回退 mg_def/1920）
            height: 拟定分辨率高度（调用方时间线尺寸优先，缺省回退 mg_def/1080）
            fps: 实际时间线帧率（写进 <html data-fps>）

        Returns:
            完整 HTML 字符串（含 <style> 和 <script>）
        """
        params = params or {}
        # 分辨率：调用方传入的时间线尺寸优先 → mg_def 兜底 → 1920x1080
        w = width or mg_def.get("width", 1920)
        h = height or mg_def.get("height", 1080)
        dur = mg_def.get("duration_sec", 3.0)
        bg = MGRenderer._sanitize_css_value(
            mg_def.get("style", {}).get("background", "transparent")
        )
        font_family = MGRenderer._sanitize_css_value(
            mg_def.get("style", {}).get("font_family", "sans-serif")
        )
        variables = MGRenderer.build_variables(mg_def, params)
        bound_ids = {str(v["id"]) for v in variables}

        elements_html = []
        css_animations = []
        js_code = []

        for i, elem in enumerate(mg_def.get("elements", [])):
            result = MGRenderer._render_element(elem, params, i, dur, bound_ids=bound_ids)
            if result:
                elements_html.append(result["html"])
                css_animations.append(result["css"])
                js_code.append(result["js"])

        all_css = "\n".join(css_animations)
        all_js = "\n".join(js_code)
        # 审计 HFR-2：注册真实 RuntimeTimelineLike（替代旧的 {{paused:true}} 空对象桩）。
        # 上游 init.ts 明确允许「部分实现」（duration/seek 即可），渲染路径只 seek 不 pause；
        # 注册后 producer 不再等 timeline 就绪轮询（实测每次渲染省 45s+）。
        timeline_js = timeline_script()

        return f"""<!DOCTYPE html>
<html data-fps="{fps:g}" data-width="{w}" data-height="{h}"
      data-composition-variables="{MGRenderer._json_attr(variables)}">
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
{timeline_js}
</script>
</body></html>"""

    @staticmethod
    def _render_element(
        elem: dict, params: dict, idx: int, total_dur: float,
        bound_ids: set[str] | None = None,
    ) -> dict | None:
        """渲染单个元素 → {html, css, js}。

        bound_ids: 已声明为 HyperFrames 变量的参数键；这些键在**整个值就是一个占位符**时
        会渲染成 `var(--key, 兜底值)`（CSS 属性）或 `data-var-text="key"`（文本），
        从而支持 `--variables` 覆盖；其余情况仍内联替换，行为与旧实现一致。
        """
        bound_ids = bound_ids or set()
        elem_type = elem.get("type", "text")
        kfs = elem.get("keyframes", [])
        if not kfs:
            # 审计 P2 修复：静默丢弃改 warning，便于线上排查残缺动画
            logger.warning("MG 元素 #%d (%s) 无关键帧，已跳过", idx, elem.get("type", "text"))
            return None

        eid = f"mg-e{idx}"
        anim_name = f"mg_anim_{idx}"

        # 替换占位符
        def fill(val: Any) -> Any:
            if isinstance(val, str):
                for k, v in params.items():
                    val = val.replace("{" + k + "}", str(v))
            return val

        def bindable(val: Any) -> Any:
            """先尝试变量绑定，再退回内联替换。"""
            return MGRenderer._bind_or_fill(val, params, bound_ids)

        # 文本绑定：内容整体就是一个占位符 → data-var-text（保留解析后文本作兜底）
        var_text_id = ""
        raw_content = elem.get("content", "")
        if elem_type == "text" and isinstance(raw_content, str):
            m = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", raw_content.strip())
            if m and m.group(1) in bound_ids:
                var_text_id = m.group(1)

        if var_text_id:
            # 兜底文本取解析值；值为空时用空串——绝不能把 `{key}` 字面量留在画面上
            content = str(params.get(var_text_id, "") or "")
        else:
            content = fill(raw_content)

        # 位置计算
        x = elem.get("x", "center")
        y = elem.get("y", "center")
        x_off = elem.get("x_offset", 0)
        y_off = elem.get("y_offset", 0)

        if x == "center":
            # center 定位必须应用 x_offset（与 y 分支对称：y=center 用 calc(50% + y_off)）。
            # 此前忽略 x_offset → LLM 模板用 x_offset 分列 left/right 标签时渲染仍重叠
            # （质检发现 mg_generated_cost_asymmetry 的 {left_label}/{right_label}）。
            # offset 为 0 时保持原 left:50% 输出（向后兼容既有模板）。
            if x_off:
                left_style = f"left:calc(50% + {x_off}px)"
            else:
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
            # 审计 P0 修复 + 约定统一：edge 锚点一律「正 offset = 远离边缘（向画布内）」。
            # 与 x=left(left:20+x_off) / x=right(right:20+x_off) / y=top(top:20+y_off) 对称；
            # 旧式 60-y_off 使模板中 23 处正 offset 渲染到画布外。模板负值已同步翻转。
            top_style = f"bottom:{60 + y_off}px"
            xform_y = ""
        else:
            top_style = f"top:{y}px"
            xform_y = ""

        base_transform = f"{xform_x} {xform_y}".strip()
        # 审计修复（V3 验证时发现）：`bg` 是 `inset:0` 的全幅底层，**没有** center 锚点的
        # `translate(-50%,-50%)`。旧实现把 base_transform 一并写进关键帧 → 背景层被平移
        # 自身尺寸的一半，只剩**左上四分之一**可见（实测 alpha 覆盖 25%：1920×1080 只剩
        # 960×540，正是 `verify_mg_fastpath` 一开始误判为"Chrome 裁剪"的那 53%）。
        anchor_transform = "" if elem_type == "bg" else base_transform

        # 构建 CSS keyframes
        css_parts = [f"@keyframes {anim_name}{{"]
        for kf in kfs:
            # 审计 P1 修复：关键帧时间防御性钳制到 [0, total_dur]，
            # 越界 time 会生成 >100% 的 CSS stop，被 Chromium 忽略导致尾部动画不可预测
            kf_time = max(0.0, min(float(kf.get("time", 0)), max(total_dur, 0.01)))
            pct = (kf_time / max(total_dur, 0.01)) * 100
            props = {k: v for k, v in kf.items() if k != "time"}
            transforms = []
            style_attrs = []

            if "scale" in props:
                s = props.pop("scale")
                transforms.append(f"scale({fill(s)})")
            if "rotate" in props:
                r = props.pop("rotate")
                transforms.append(f"rotate({fill(r)}deg)")
            if "translate_y" in props:
                ty = props.pop("translate_y")
                transforms.append(f"translateY({fill(ty)}px)")
            if "translate_x" in props:
                tx = props.pop("translate_x")
                transforms.append(f"translateX({fill(tx)}px)")
            if "width" in props:
                w = props.pop("width")
                style_attrs.append(f"width:{fill(w)}px;")

            # 逐关键帧 easing → animation-timing-function（CSS 规范特性，Chromium/Hyperframes 支持）
            easing = props.pop("easing", None)
            timing = f"animation-timing-function:{MGRenderer._css_timing(easing)};" if easing else ""

            tf = f"{anchor_transform} {' '.join(transforms)}".strip()
            stop = f"  {pct:.1f}%{{"
            if tf:
                stop += f"transform:{tf};"
            for k, v in props.items():
                css_key = MGRenderer.KEYFRAME_CSS_MAP.get(k, k)
                stop += f"{css_key}:{bindable(MGRenderer._sanitize_css_value(v))};"
            stop += "".join(style_attrs)
            stop += timing
            stop += "}"
            css_parts.append(stop)
        css_parts.append("}")
        css = "\n".join(css_parts)

        # 构建元素 HTML
        base_css = (
            f"position:absolute;{left_style};{top_style};"
            f"transform:{base_transform};"
        )

        # 静态样式透传（非关键帧动画的固定属性）
        static_style = ""
        for k, v in elem.items():
            if k in MGRenderer.STATIC_EXCLUDE_KEYS:
                continue
            css_key = MGRenderer.KEYFRAME_CSS_MAP.get(k)
            if css_key and isinstance(v, (str, int, float)):
                static_style += f"{css_key}:{bindable(MGRenderer._sanitize_css_value(v))};"

        if elem_type == "text":
            font_size = fill(elem.get("font_size", 48))
            font_color = bindable(elem.get("font_color", "#ffffff"))
            font_weight = fill(elem.get("font_weight", "normal"))
            var_attr = f' data-var-text="{var_text_id}"' if var_text_id else ""
            html = (
                f'<div id="{eid}" class="mg-el clip" data-start="0" data-duration="{total_dur}" data-track-index="1"{var_attr} style="{base_css}'
                f'font-size:{font_size}px;color:{font_color};font-weight:{font_weight}'
                + static_style
                + f'">{MGRenderer._esc(content)}</div>'
            )
        elif elem_type == "bg":
            # 全幅背景/渐变底层：铺满、置于最底层
            color = elem.get("background") or elem.get("color", "#0e101a")
            html = (
                f'<div id="{eid}" class="mg-el clip" data-start="0" data-duration="{total_dur}" data-track-index="1" style="position:absolute;inset:0;z-index:0;'
                f'background:{bindable(MGRenderer._sanitize_css_value(color))};'
                + static_style
                + '"></div>'
            )
        elif elem_type in ("line", "circle", "ring", "arc", "shape"):
            shape = elem.get("shape", "rect")
            color = elem.get("color", "#4f8cff")
            sw = fill(elem.get("stroke_width", elem.get("border_width", 0)))
            sw_color = elem.get("stroke_color", elem.get("border_color", color))
            w_val = fill(elem.get("width", 100))
            h_val = fill(elem.get("height", 100))
            border_radius = elem.get("border_radius", 0)

            if elem_type == "circle":
                # 正圆
                border_radius = "50%"
            elif elem_type == "ring":
                # 空心圆环：border-radius 50% + border 描边
                border_radius = "50%"
            elif elem_type == "arc":
                # 近似圆弧：border 上/右圆角（非真弧）
                border_radius = "50% 50% 0 0"
            elif elem_type == "line":
                # 细长线条
                border_radius = elem.get("border_radius", 0)
            elif shape == "ellipse":
                border_radius = "50%"
            else:
                border_radius = elem.get("border_radius", 4)

            radius_css = f"border-radius:{fill(border_radius)};" if border_radius not in (0, "0", 0.0) else ""
            # 审计 P3 修复：stroke_width=0 为合法「显式无边框」，按数值判断而非 truthy
            try:
                sw_num = float(sw)
            except (TypeError, ValueError):
                sw_num = 0.0
            if elem_type == "ring":
                # ring 需要空心底色透明，用 border 画圆环
                bg_css = "background:transparent;"
            else:
                bg_val = elem.get("background") or color
                bg_css = f"background:{bindable(MGRenderer._sanitize_css_value(bg_val))};"

            html = (
                f'<div id="{eid}" class="mg-el mg-shape clip" data-start="0" data-duration="{total_dur}" data-track-index="1" style="{base_css}'
                f'width:{w_val}px;height:{h_val}px;{bg_css}'
                f'{radius_css}'
                + (f'border:{sw}px solid {bindable(sw_color)};' if sw_num > 0 else "")
                + static_style
                + '"></div>'
            )
        elif elem_type == "image":
            # 图片元素：<img> 渲染。src 为图片资源路径（素材库/本地文件/URL），
            # x/y 定位、width/height 尺寸；opacity/scale/translate 等关键帧动画
            # 复用上方通用 keyframe 机制（与 text/shape 等元素一致）。
            src = MGRenderer._safe_image_src(str(fill(elem.get("src", ""))))
            if not src:
                logger.warning(
                    "MG 元素 #%d 图片 src 被拒绝（D12：http(s)/越权路径/穿越路径）", idx
                )
                return None
            # P1-5：src 整体是占位符时绑定为变量（保留解析后的 src 作兜底，
            # 上游 lint 的 media_variable_src_no_fallback 正是要求兜底存在）
            var_src_attr = ""
            _raw_src = elem.get("src", "")
            if isinstance(_raw_src, str):
                _m = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _raw_src.strip())
                if _m and _m.group(1) in bound_ids:
                    var_src_attr = f' data-var-src="{_m.group(1)}"'
            # 本地文件路径转 file:/// URI（headless Chrome 相对解析会裂图）
            w_val = fill(elem.get("width", 320))
            h_val = fill(elem.get("height", 240))
            radius_css = ""
            br = elem.get("border_radius", 0)
            if br not in (0, "0", 0.0):
                radius_css = f"border-radius:{fill(br)};"
            html = (
                f'<img id="{eid}" class="mg-el mg-image clip" data-start="0" data-duration="{total_dur}" data-track-index="1"{var_src_attr} '
                f'src="{MGRenderer._esc(str(src))}" alt="" '
                f'style="{base_css}width:{w_val}px;height:{h_val}px;'
                f'object-fit:contain;{radius_css}'
                + static_style
                + '"/>'
            )
        else:
            # 审计 P2 修复：未知元素类型静默丢弃改 warning
            logger.warning("MG 元素 #%d 类型 %r 不受支持，已跳过", idx, elem_type)
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
