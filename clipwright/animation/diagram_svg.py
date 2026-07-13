"""DiagramSVG — SVG 图解渲染引擎。

所有逻辑图解（箭头/对比/流程/时间线/树状/维恩/柱状图）的 SVG 生成。

核心设计：
- 逐元素入场动画（staggered entrance）
- CSS 渐变、阴影、圆角（依赖 CSS，SVG 内部不硬编码）
- 样式系统可被 Persona 配色覆盖
- 插件可通过 HookRegistry 注册自定义图解类型
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from clipwright.config import logger


@dataclass
class DiagramStyle:
    """图解视觉风格配置，来源：Persona visual_config → AnimationAgent → 渲染器。"""
    primary_color: str = "#4f8cff"
    secondary_color: str = "#ff6b6b"
    accent_color: str = "#fbbf24"
    bg_color: str = "rgba(255,255,255,0.10)"
    text_color: str = "#ffffff"
    item_bg: str = "rgba(255,255,255,0.12)"
    item_bg_alt: str = "rgba(79,140,255,0.15)"
    font_size: int = 28
    title_font_size: int = 36
    stagger_delay: float = 0.25
    border_radius: int = 12
    arrow_color: str = "#4f8cff"
    vs_color: str = "#ff6b6b"

    @classmethod
    def from_persona(cls, persona_style: dict | None = None) -> DiagramStyle:
        """从 Persona 视觉配置创建，合并插件注册的 style preset。"""
        base = cls()
        if not persona_style:
            persona_style = {}

        # 读取插件注册的 style presets
        plugin_presets: dict[str, dict] = {}
        try:
            from clipwright.plugins.hooks import HookRegistry, HookPoint
            ctx = HookRegistry.execute(HookPoint.DIAGRAM_STYLE_PRESET, {})
            presets = ctx.get("presets", {})
            if isinstance(presets, dict):
                plugin_presets = presets
        except Exception:
            pass

        # 如果指定了 preset 名称，优先从插件 preset 加载
        preset_name = persona_style.get("style_preset", "")
        if preset_name and preset_name in plugin_presets:
            base = cls(**{**cls.__dict__, **plugin_presets[preset_name]})

        return cls(
            primary_color=persona_style.get("primary_color", base.primary_color),
            secondary_color=persona_style.get("secondary_color", base.secondary_color),
            accent_color=persona_style.get("accent_color", base.accent_color),
            text_color=persona_style.get("font_color", base.text_color),
            font_size=persona_style.get("font_size", base.font_size),
            title_font_size=persona_style.get("title_font_size", base.title_font_size),
            stagger_delay=persona_style.get("stagger_delay", base.stagger_delay),
        )


class DiagramRenderer:
    """SVG 图解渲染器。"""

    # 插件注册的自定义渲染器
    _custom_renderers: dict[str, Callable] = {}

    @classmethod
    def register_renderer(cls, name: str, renderer: Callable) -> None:
        cls._custom_renderers[name] = renderer

    @classmethod
    def render(cls, params: dict, style: DiagramStyle | None = None,
               width: int = 1920, height: int = 1080) -> str:
        """渲染入口：根据 preset 选择渲染器。"""
        if style is None:
            style = DiagramStyle()
        preset = params.get("preset", "diagram")
        text = params.get("title", "")
        items = params.get("items", [])

        renderer_map: dict[str, Callable] = {
            "diagram": cls._arrow,
            "causation": cls._arrow,
            "comparison": cls._comparison,
            "sequence": cls._sequence,
            "timeline": cls._timeline,
            "tree": cls._tree,
            "hierarchy": cls._tree,
            "venn": cls._venn,
            "bar_chart": cls._bar_chart,
            "pie_chart": cls._pie_chart,
            "line_chart": cls._line_chart,
            "sequence_diagram": cls._sequence_diagram,
            "flow_chart": cls._flow_chart,
        }
        # 插件自定义
        renderer_map.update(cls._custom_renderers)

        fn = renderer_map.get(preset)
        if fn:
            return fn(items, text, style, width, height)
        logger.warning("DiagramRenderer: 未知 preset=%s，回退到箭头", preset)
        return cls._arrow(items, text, style, width, height)

    @classmethod
    def get_supported_presets(cls) -> list[dict[str, str]]:
        """返回所有支持的图解类型（内置 + 插件注册，用于 StructureAgent prompt + 前端）。"""
        presets = [
            {"id": "diagram", "name": "箭头", "desc": "展示因果关系 A→B→C"},
            {"id": "causation", "name": "因果", "desc": "因果链条 A → 导致 → B"},
            {"id": "comparison", "name": "对比", "desc": "对比两个事物 A vs B"},
            {"id": "sequence", "name": "流程", "desc": "步骤/流程/序列"},
            {"id": "timeline", "name": "时间线", "desc": "历史演进/项目里程碑"},
            {"id": "tree", "name": "层级", "desc": "分类/组织结构/目录树"},
            {"id": "hierarchy", "name": "层级", "desc": "同 tree"},
            {"id": "venn", "name": "维恩", "desc": "交集/包含关系"},
            {"id": "bar_chart", "name": "柱状图", "desc": "数据对比"},
            {"id": "pie_chart", "name": "饼图", "desc": "占比分布"},
            {"id": "line_chart", "name": "折线图", "desc": "趋势变化"},
            {"id": "sequence_diagram", "name": "序列图", "desc": "参与者消息传递顺序"},
            {"id": "flow_chart", "name": "流程图", "desc": "判断/分支/循环逻辑结构"},
        ]
        # 插件注册的自定义渲染器
        for cid, cfn in cls._custom_renderers.items():
            presets.append({"id": cid, "name": cid, "desc": getattr(cfn, "__doc__", "") or ""})
        # 通过 Hook 注册的自定义渲染器
        try:
            from clipwright.plugins.hooks import HookRegistry, HookPoint
            ctx = HookRegistry.execute(HookPoint.DIAGRAM_RENDERER_EXTEND, {})
            renderers = ctx.get("renderers", [])
            if isinstance(renderers, list):
                for r in renderers:
                    if isinstance(r, dict) and "id" in r:
                        presets.append({
                            "id": r["id"],
                            "name": r.get("name", r["id"]),
                            "desc": r.get("desc", ""),
                        })
                        cls._custom_renderers[r["id"]] = r.get("renderer", lambda *a, **kw: "")
        except Exception:
            pass
        return presets

    # ── SVG 辅助 ────────────────────────────────────────

    @staticmethod
    def _svg_frame(width: int, height: int, anim_delay: float = 0) -> str:
        return (f'<svg width="{width}" height="{height}"'
                f' style="position:absolute;top:0;left:0;animation-delay:{anim_delay}s;'
                f'animation-duration:0.5s">')

    @staticmethod
    def _staggered(items: list, delay: float, offset: float = 0.25) -> list[float]:
        """逐元素动画延迟。"""
        return [delay + i * offset for i in range(len(items))]

    @staticmethod
    def _html_esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # ── 富文本标记解析 ──────────────────────────────────

    @staticmethod
    def _rich_to_tspans(markup: str, font_size: int, fill: str) -> list[str]:
        """将简易标记转为 SVG <tspan> 列表。

        标记语法: [red]红字[/red][bold]加粗[/bold][size=72]大字[/size]
        支持: red, blue, yellow, green, orange, bold, size=N
        """
        import re
        tspans: list[str] = []
        pos = 0
        cur_color = fill
        cur_bold = ""
        cur_size = font_size

        for m in re.finditer(r'\[(red|blue|yellow|green|orange|bold|size=\d+|/[\w]*)\]', markup):
            if m.start() > pos:
                text = DiagramRenderer._html_esc(markup[pos:m.start()])
                tspans.append(
                    f'<tspan fill="{cur_color}" font-size="{cur_size}" '
                    f'font-weight="{cur_bold or "normal"}">{text}</tspan>'
                )
            tag = m.group(1)
            if tag == "red": cur_color = "#ff4444"
            elif tag == "blue": cur_color = "#4488ff"
            elif tag == "yellow": cur_color = "#ffd700"
            elif tag == "green": cur_color = "#44cc44"
            elif tag == "orange": cur_color = "#ff8800"
            elif tag == "bold": cur_bold = "bold"
            elif tag.startswith("size="):
                try: cur_size = int(tag.split("=")[1])
                except: pass
            elif tag.startswith("/"):
                cur_color = fill
                cur_bold = ""
                cur_size = font_size
            pos = m.end()

        if pos < len(markup):
            text = DiagramRenderer._html_esc(markup[pos:])
            tspans.append(
                f'<tspan fill="{cur_color}" font-size="{cur_size}" '
                f'font-weight="{cur_bold or "normal"}">{text}</tspan>'
            )
        return tspans

    # ── 缓动函数 ────────────────────────────────────────

    @staticmethod
    def _easing(t: float, easing: str = "linear") -> float:
        """三次贝塞尔缓动。支持 linear / ease_in / ease_out / ease_in_out / bounce。"""
        def _bez(t2: float, x1: float, y1: float, x2: float, y2: float) -> float:
            # 牛顿法求 x 对应的 t，再采样 y
            lo, hi = 0.0, 1.0
            for _ in range(20):
                mid = (lo + hi) / 2
                if 3 * x1 * mid * (1 - mid) ** 2 + 3 * x2 * mid ** 2 * (1 - mid) + mid ** 3 < t2:
                    lo = mid
                else:
                    hi = mid
            ct = (lo + hi) / 2
            return 3 * y1 * ct * (1 - ct) ** 2 + 3 * y2 * ct ** 2 * (1 - ct) + ct ** 3

        presets = {
            "linear": (0, 0, 1, 1),
            "ease_in": (0.42, 0, 1, 1),
            "ease_out": (0, 0, 0.58, 1),
            "ease_in_out": (0.42, 0, 0.58, 1),
            "bounce": (0.68, -0.55, 0.27, 1.55),
        }
        params = presets.get(easing, presets["linear"])
        return _bez(t, *params)

    # ── 箭头/因果图解 ───────────────────────────────────

    @classmethod
    def _arrow(cls, items: list[str], title: str, s: DiagramStyle,
               w: int, h: int) -> str:
        """A → B → C 箭头图解，带逐元素入场。"""
        n = min(len(items), 6)
        if n == 0:
            return ""
        cx, cy = w // 2, h // 2
        spacing = min(260, (w - 200) // max(n, 1))
        total_w = (n - 1) * spacing + 180
        sx = cx - total_w // 2
        delays = cls._staggered(range(n), 0, s.stagger_delay)

        parts = [cls._svg_frame(w, h)]

        # 标题
        if title:
            parts.append(
                f'<text x="{cx}" y="{cy - 80}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 箭头组 + 节点组（逐元素出现）
        for i in range(n):
            x = sx + i * spacing
            # 节点矩形（带阴影）
            shadow = 'filter="url(#ds)"' if s.stagger_delay > 0 else ""
            parts.append(
                f'<g style="animation-delay:{delays[i]}s">'
                f'<rect x="{x}" y="{cy - 30}" width="170" height="60" rx="{s.border_radius}"'
                f' fill="{s.item_bg}" stroke="{s.primary_color}" stroke-width="1" {shadow}/>'
                f'<text x="{x + 85}" y="{cy + 6}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle">'
                f'{cls._html_esc(items[i][:16])}</text></g>'
            )
            # 箭头（两个节点之间）
            if i < n - 1:
                ax = x + 170
                ay = cy
                parts.append(
                    f'<g style="animation-delay:{delays[i] + s.stagger_delay * 0.5}s">'
                    f'<line x1="{ax}" y1="{ay}" x2="{ax + spacing - 170}" y2="{ay}"'
                    f' stroke="{s.arrow_color}" stroke-width="3" marker-end="url(#a)"/>'
                    f'<text x="{ax + (spacing - 170) // 2}" y="{ay - 10}" font-size="20"'
                    f' fill="{s.arrow_color}" text-anchor="middle">→</text></g>'
                )

        # 箭头 marker 定义
        marker = ('<defs>'
                  '<filter id="ds"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.3"/></filter>'
                  '<marker id="a" viewBox="0 0 10 10" refX="10" refY="5"'
                  ' markerWidth="8" markerHeight="8" orient="auto">'
                  '<path d="M0,0L10,5L0,10Z" fill="' + s.arrow_color + '"/></marker>'
                  '</defs>')
        parts.insert(1, marker)

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 对比图解 ────────────────────────────────────────

    @classmethod
    def _comparison(cls, items: list[str], title: str, s: DiagramStyle,
                    w: int, h: int) -> str:
        """左右对比图解。"""
        left = items[0] if len(items) > 0 else ""
        right = items[1] if len(items) > 1 else ""
        cx, cy = w // 2, h // 2
        delays = cls._staggered([0, 1, 2], 0, s.stagger_delay)

        parts = [
            cls._svg_frame(w, h),
            '<defs>'
            '<filter id="ds2"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.3"/></filter>'
            '<linearGradient id="gradL" x1="0%" y1="0%" x2="100%" y2="100%">'
            f'<stop offset="0%" stop-color="{s.primary_color}44"/>'
            f'<stop offset="100%" stop-color="{s.primary_color}88"/>'
            '</linearGradient>'
            '<linearGradient id="gradR" x1="0%" y1="0%" x2="100%" y2="100%">'
            f'<stop offset="0%" stop-color="{s.secondary_color}44"/>'
            f'<stop offset="100%" stop-color="{s.secondary_color}88"/>'
            '</linearGradient>'
            '</defs>',
        ]

        if title:
            parts.append(
                f'<text x="{cx}" y="{cy - 100}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 左
        if left:
            parts.append(
                f'<g style="animation-delay:{delays[0]}s">'
                f'<rect x="{cx - 280}" y="{cy - 40}" width="240" height="80" rx="{s.border_radius}"'
                f' fill="url(#gradL)" filter="url(#ds2)"/>'
                f'<text x="{cx - 160}" y="{cy + 6}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle">'
                f'{cls._html_esc(left[:20])}</text></g>'
            )
        # VS
        parts.append(
            f'<g style="animation-delay:{delays[1]}s">'
            f'<text x="{cx}" y="{cy + 6}" font-size="36" fill="{s.vs_color}"'
            f' text-anchor="middle" font-weight="bold">VS</text></g>'
        )
        # 右
        if right:
            parts.append(
                f'<g style="animation-delay:{delays[2]}s">'
                f'<rect x="{cx + 40}" y="{cy - 40}" width="240" height="80" rx="{s.border_radius}"'
                f' fill="url(#gradR)" filter="url(#ds2)"/>'
                f'<text x="{cx + 160}" y="{cy + 6}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle">'
                f'{cls._html_esc(right[:20])}</text></g>'
            )

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 流程/步骤图解 ──────────────────────────────────

    @classmethod
    def _sequence(cls, items: list[str], title: str, s: DiagramStyle,
                  w: int, h: int) -> str:
        """垂直步骤列表，每步带编号。"""
        cx = w // 2
        top_y = h // 2 - 80
        delays = cls._staggered(range(len(items)), 0, s.stagger_delay)
        parts = [cls._svg_frame(w, h)]

        if title:
            parts.append(
                f'<text x="{cx}" y="{top_y - 30}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        for i, item in enumerate(items[:8]):
            yy = top_y + i * 50
            # 编号圆
            parts.append(
                f'<g style="animation-delay:{delays[i]}s">'
                f'<circle cx="{cx - 120}" cy="{yy}" r="14" fill="{s.primary_color}"/>'
                f'<text x="{cx - 120}" y="{yy + 5}" font-size="14" fill="#fff"'
                f' text-anchor="middle" font-weight="bold">{i + 1}</text>'
                f'<text x="{cx - 90}" y="{yy + 5}" font-size="{s.font_size}"'
                f' fill="{s.text_color}">{cls._html_esc(item[:40])}</text></g>'
            )

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 时间线图解 ──────────────────────────────────────

    @classmethod
    def _timeline(cls, items: list[str], title: str, s: DiagramStyle,
                  w: int, h: int) -> str:
        """水平时间线，节点沿轴线分布。"""
        n = min(len(items), 8)
        if n == 0:
            return ""
        cx, cy = w // 2, h // 2
        spacing = min(220, (w - 160) // max(n, 1))
        total_w = (n - 1) * spacing
        sx = cx - total_w // 2
        delays = cls._staggered(range(n), 0, s.stagger_delay)
        parts = [cls._svg_frame(w, h)]

        if title:
            parts.append(
                f'<text x="{cx}" y="{cy - 90}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 轴线
        parts.append(
            f'<line x1="{sx - 20}" y1="{cy}" x2="{sx + total_w + 20}" y2="{cy}"'
            f' stroke="{s.primary_color}44" stroke-width="2" stroke-dasharray="6,4"/>'
        )

        for i in range(n):
            x = sx + i * spacing
            parts.append(
                f'<g style="animation-delay:{delays[i]}s">'
                # 节点圆
                f'<circle cx="{x}" cy="{cy}" r="8" fill="{s.primary_color}"'
                f' stroke="#fff" stroke-width="2"/>'
                # 标签（上下交替）
                f'<text x="{x}" y="{cy - 22 if i % 2 == 0 else cy + 42}"'
                f' font-size="{s.font_size}" fill="{s.text_color}"'
                f' text-anchor="middle">{cls._html_esc(items[i][:15])}</text></g>'
            )

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 树状/层级图解 ──────────────────────────────────

    @classmethod
    def _tree(cls, items: list[str], title: str, s: DiagramStyle,
              w: int, h: int) -> str:
        """自上而下的树状结构。第一项为根节点。"""
        n = min(len(items), 9)
        if n == 0:
            return ""
        cx = w // 2
        delays = cls._staggered(range(n), 0, s.stagger_delay)
        parts = [cls._svg_frame(w, h),
                 '<defs><filter id="ds3"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.3"/></filter></defs>']

        # 根节点
        parts.append(
            f'<g style="animation-delay:{delays[0]}s">'
            f'<rect x="{cx - 70}" y="200" width="140" height="50" rx="{s.border_radius}"'
            f' fill="{s.primary_color}44" stroke="{s.primary_color}" filter="url(#ds3)"/>'
            f'<text x="{cx}" y="230" font-size="{s.font_size}" fill="{s.text_color}"'
            f' text-anchor="middle">{cls._html_esc(items[0][:14])}</text></g>'
        )

        # 子节点（水平排列）
        children = items[1:]
        n_child = len(children)
        spacing = min(220, (w - 100) // max(n_child, 1))
        ch_total = (n_child - 1) * spacing if n_child > 1 else 0
        ch_sx = cx - ch_total // 2

        for i, child in enumerate(children[:8]):
            x = ch_sx + i * spacing
            # 连接线
            parts.append(
                f'<g style="animation-delay:{delays[i + 1]}s">'
                f'<line x1="{cx}" y1="250" x2="{x}" y2="310"'
                f' stroke="{s.primary_color}44" stroke-width="1.5"/>'
                f'<rect x="{x - 60}" y="310" width="120" height="44" rx="{s.border_radius}"'
                f' fill="{s.item_bg}" stroke="{s.primary_color}66" filter="url(#ds3)"/>'
                f'<text x="{x}" y="336" font-size="{s.font_size}" fill="{s.text_color}"'
                f' text-anchor="middle">{cls._html_esc(child[:12])}</text></g>'
            )

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 维恩图解 ────────────────────────────────────────

    @classmethod
    def _venn(cls, items: list[str], title: str, s: DiagramStyle,
              w: int, h: int) -> str:
        """两个重叠的维恩图。"""
        cx, cy = w // 2, h // 2
        r = 140
        delays = cls._staggered([0, 1, 2], 0, s.stagger_delay)
        left = items[0] if len(items) > 0 else ""
        right = items[1] if len(items) > 1 else ""
        overlap = items[2] if len(items) > 2 else ""

        parts = [
            cls._svg_frame(w, h),
            '<defs>'
            '<filter id="ds4"><feDropShadow dx="0" dy="2" stdDeviation="4" flood-opacity="0.2"/></filter>'
            f'<clipPath id="vl"><circle cx="{cx - 60}" cy="{cy}" r="{r}"/></clipPath>'
            f'<clipPath id="vr"><circle cx="{cx + 60}" cy="{cy}" r="{r}"/></clipPath>'
            '</defs>',
        ]

        if title:
            parts.append(
                f'<text x="{cx}" y="{cy - r - 40}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 左圆
        parts.append(
            f'<circle cx="{cx - 60}" cy="{cy}" r="{r}" fill="{s.primary_color}33"'
            f' stroke="{s.primary_color}" stroke-width="2" filter="url(#ds4)"'
            f' style="animation-delay:{delays[0]}s"/>'
        )
        # 右圆
        parts.append(
            f'<circle cx="{cx + 60}" cy="{cy}" r="{r}" fill="{s.secondary_color}33"'
            f' stroke="{s.secondary_color}" stroke-width="2" filter="url(#ds4)"'
            f' style="animation-delay:{delays[1]}s"/>'
        )

        # 左标签
        if left:
            parts.append(
                f'<text x="{cx - 100}" y="{cy}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle"'
                f' style="animation-delay:{delays[0]}s">'
                f'{cls._html_esc(left[:12])}</text>')
        # 重叠标签
        if overlap:
            parts.append(
                f'<text x="{cx}" y="{cy}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle"'
                f' style="animation-delay:{delays[2]}s">'
                f'{cls._html_esc(overlap[:10])}</text>')
        # 右标签
        if right:
            parts.append(
                f'<text x="{cx + 100}" y="{cy}" font-size="{s.font_size}"'
                f' fill="{s.text_color}" text-anchor="middle"'
                f' style="animation-delay:{delays[1]}s">'
                f'{cls._html_esc(right[:12])}</text>')

        parts.append('</svg>')
        return "\n".join(parts)

    # ── 柱状图 ───────────────────────────────────────────

    @classmethod
    def _bar_chart(cls, items: list[str], title: str, s: DiagramStyle,
                   w: int, h: int) -> str:
        """垂直柱状图，每项格式：标签或标签:数值。"""
        n = min(len(items), 8)
        if n == 0:
            return ""
        cx, cy = w // 2, h // 2 + 60
        chart_w = min(800, w - 100)
        bar_w = chart_w // n - 20
        sx = cx - chart_w // 2
        chart_h = 300
        delays = cls._staggered(range(n), 0, s.stagger_delay)

        # 解析数值（标签:数值 或纯标签）
        parsed = []
        for item in items[:8]:
            if ":" in item:
                parts_s = item.split(":", 1)
                parsed.append((parts_s[0], float(parts_s[1])))
            else:
                parsed.append((item, 50 + hash(item) % 50))
        max_val = max(v for _, v in parsed) or 1

        parts_svg = [cls._svg_frame(w, h)]

        if title:
            parts_svg.append(
                f'<text x="{cx}" y="{cy - chart_h - 40}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 轴线
        parts_svg.append(
            f'<line x1="{sx - 10}" y1="{cy}" x2="{sx + chart_w + 10}" y2="{cy}"'
            f' stroke="{s.text_color}44" stroke-width="1"/>'
        )

        colors = [s.primary_color, s.secondary_color, s.accent_color,
                  "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899"]

        for i, (label, val) in enumerate(parsed):
            x = sx + i * (chart_w // n)
            bar_h = (val / max_val) * chart_h
            color = colors[i % len(colors)]
            parts_svg.append(
                f'<g style="animation-delay:{delays[i]}s">'
                f'<rect x="{x + 10}" y="{cy - bar_h}" width="{bar_w}" height="{bar_h}"'
                f' rx="4" fill="{color}88" stroke="{color}" stroke-width="1"/>'
                f'<text x="{x + 10 + bar_w // 2}" y="{cy - bar_h - 8}" font-size="16"'
                f' fill="{s.text_color}" text-anchor="middle">{val:.0f}</text>'
                f'<text x="{x + 10 + bar_w // 2}" y="{cy + 20}" font-size="14"'
                f' fill="{s.text_color}" text-anchor="middle" transform="rotate(-15,{x + 10 + bar_w // 2},{cy + 20})">'
                f'{cls._html_esc(label[:8])}</text></g>'
            )

        parts_svg.append('</svg>')
        return "\n".join(parts_svg)

    # ── 饼图 ─────────────────────────────────────────────

    @classmethod
    def _pie_chart(cls, items: list[str], title: str, s: DiagramStyle,
                   w: int, h: int) -> str:
        """饼图，每项格式：标签:数值 或纯标签（等分）。"""
        n = min(len(items), 8)
        if n == 0:
            return ""
        cx, cy = w // 2, h // 2 - 20
        r = 160
        delays = cls._staggered(range(n), 0, s.stagger_delay)

        parsed = []
        for item in items[:8]:
            if ":" in item:
                ps = item.split(":", 1)
                parsed.append((ps[0], float(ps[1])))
            else:
                parsed.append((item, 100 / n))
        total = sum(v for _, v in parsed) or 1

        colors = [s.primary_color, s.secondary_color, s.accent_color,
                  "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899"]

        parts_svg = [cls._svg_frame(w, h), '<defs>']
        for i in range(n):
            color = colors[i % len(colors)]
            parts_svg.append(
                f'<linearGradient id="pg{i}" x1="0%" y1="0%" x2="100%" y2="100%">'
                f'<stop offset="0%" stop-color="{color}66"/>'
                f'<stop offset="100%" stop-color="{color}bb"/>'
                f'</linearGradient>'
            )
        parts_svg.append('</defs>')

        if title:
            parts_svg.append(
                f'<text x="{cx}" y="{cy - r - 40}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        # 绘制扇形
        import math
        angle_start = -90  # 12 o'clock
        cx_f, cy_f = float(cx), float(cy)

        for i, (label, val) in enumerate(parsed):
            angle = (val / total) * 360
            angle_end = angle_start + angle
            a_start_r = math.radians(angle_start)
            a_end_r = math.radians(min(angle_end, angle_start + 360))

            x1 = cx_f + r * math.cos(a_start_r)
            y1 = cy_f + r * math.sin(a_start_r)
            x2 = cx_f + r * math.cos(a_end_r)
            y2 = cy_f + r * math.sin(a_end_r)

            large = 1 if angle > 180 else 0
            color_id = f"url(#pg{i})"
            parts_svg.append(
                f'<path d="M{cx_f},{cy_f} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 {x2:.1f},{y2:.1f} Z"'
                f' fill="{color_id}" stroke="#fff" stroke-width="1"'
                f' style="animation-delay:{delays[i]}s"/>'
            )

            # 标签（扇形中间）
            mid_angle = math.radians(angle_start + angle / 2)
            lx = cx_f + (r * 0.65) * math.cos(mid_angle)
            ly = cy_f + (r * 0.65) * math.sin(mid_angle)
            parts_svg.append(
                f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="14" fill="#fff"'
                f' text-anchor="middle" font-weight="bold"'
                f' style="animation-delay:{delays[i]}s">{val:.0f}%</text>'
            )

            angle_start = angle_end if angle_end < 360 else -90

        # 图例
        legend_x = cx + r + 30
        legend_y = cy - r
        for i, (label, val) in enumerate(parsed):
            color = colors[i % len(colors)]
            ly_pos = legend_y + i * 28
            parts_svg.append(
                f'<g style="animation-delay:{delays[i]}s">'
                f'<rect x="{legend_x}" y="{ly_pos - 8}" width="14" height="14" rx="3" fill="{color}88" stroke="{color}"/>'
                f'<text x="{legend_x + 20}" y="{ly_pos + 4}" font-size="14" fill="{s.text_color}">'
                f'{cls._html_esc(label[:20])}</text></g>'
            )

        parts_svg.append('</svg>')
        return "\n".join(parts_svg)

    # ── 折线图 ───────────────────────────────────────────

    @classmethod
    def _line_chart(cls, items: list[str], title: str, s: DiagramStyle,
                    w: int, h: int) -> str:
        """折线图，每项格式：标签:数值。"""
        n = min(len(items), 12)
        if n < 2:
            return cls._bar_chart(items, title, s, w, h)
        cx, cy = w // 2, h // 2 + 40
        chart_w = min(700, w - 100)
        chart_h = 250
        sx = cx - chart_w // 2
        delays = cls._staggered(range(n), 0, s.stagger_delay * 0.8)

        parsed = []
        for item in items[:12]:
            if ":" in item:
                ps = item.split(":", 1)
                parsed.append((ps[0], float(ps[1])))
            else:
                parsed.append((item, float(50 + hash(item) % 50)))
        max_val = max(v for _, v in parsed) or 1
        min_val = min(v for _, v in parsed) or 0
        val_range = max_val - min_val or 1

        parts_svg = [cls._svg_frame(w, h), '<defs>'
                     f'<linearGradient id="lg1" x1="0%" y1="0%" x2="0%" y2="100%">'
                     f'<stop offset="0%" stop-color="{s.primary_color}66"/>'
                     f'<stop offset="100%" stop-color="{s.primary_color}00"/>'
                     f'</linearGradient></defs>']

        if title:
            parts_svg.append(
                f'<text x="{cx}" y="{cy - chart_h - 40}" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>'
            )

        step = chart_w / (n - 1) if n > 1 else chart_w
        points = []
        for i, (label, val) in enumerate(parsed):
            px = sx + i * step
            py = cy - ((val - min_val) / val_range) * chart_h
            points.append((px, py))

        # 面积填充
        area = f'M{sx},{cy}'
        for px, py in points:
            area += f' L{px:.1f},{py:.1f}'
        area += f' L{sx + chart_w},{cy} Z'
        parts_svg.append(
            f'<path d="{area}" fill="url(#lg1)" style="animation-delay:0s"/>'
        )

        # 折线 + 点（逐元素出现）
        for i, (label, val) in enumerate(parsed):
            px, py = points[i]
            parts_svg.append(
                f'<g style="animation-delay:{delays[i]}s">'
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6" fill="{s.primary_color}" stroke="#fff" stroke-width="2"/>'
                f'<text x="{px:.1f}" y="{py - 14}" font-size="13" fill="{s.text_color}" text-anchor="middle">'
                f'{val:.0f}</text>'
                f'<text x="{px:.1f}" y="{cy + 18}" font-size="12" fill="{s.text_color}66" text-anchor="middle">'
                f'{cls._html_esc(label[:6])}</text></g>'
            )
            # 线段（到下一个点）
            if i < n - 1:
                nx, ny = points[i + 1]
                parts_svg.append(
                    f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{nx:.1f}" y2="{ny:.1f}"'
                    f' stroke="{s.primary_color}" stroke-width="2"'
                    f' style="animation-delay:{delays[i]}s"/>'
                )

        parts_svg.append('</svg>')
        return "\n".join(parts_svg)

    # ── 序列图 ──────────────────────────────────────────

    @classmethod
    def _sequence_diagram(cls, items: list[str], title: str, s: DiagramStyle,
                          w: int, h: int) -> str:
        """参与者消息传递序列图。"""
        cx = w // 2
        participants = items[0].split(",") if items else ["A", "B"]
        messages = items[1:] if len(items) > 1 else []
        n = len(participants)
        spacing = min(360, (w - 100) // max(n, 1))
        sx = cx - ((n - 1) * spacing) // 2
        delays = cls._staggered(range(len(messages) + 1), 0, s.stagger_delay)
        parts = [cls._svg_frame(w, h)]
        if title:
            parts.append(
                f'<text x="{cx}" y="60" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>')
        # 参与者头部 + 生命线
        for i, name in enumerate(participants):
            x = sx + i * spacing
            parts.append(
                f'<g style="animation-delay:{delays[0]}s">'
                f'<rect x="{x - 60}" y="100" width="120" height="36" rx="18"'
                f' fill="{s.primary_color}44" stroke="{s.primary_color}" filter="url(#ds_sd)"/>'
                f'<text x="{x}" y="123" font-size="18" fill="{s.text_color}" text-anchor="middle">'
                f'{cls._html_esc(name[:10])}</text>'
                f'<line x1="{x}" y1="136" x2="{x}" y2="600" stroke="{s.text_color}22" stroke-width="1" stroke-dasharray="4,3"/>'
                f'</g>')
        # 消息箭头
        for j, msg_raw in enumerate(messages):
            parts_raw = msg_raw.split(":", 1)
            spec, label = parts_raw[0], (parts_raw[1] if len(parts_raw) > 1 else "")
            fi = int(spec.split("->")[0]) if "->" in spec else 0
            ti = int(spec.split("->")[1]) if "->" in spec else 1
            fy = sx + min(fi, n - 1) * spacing
            ty = sx + min(ti, n - 1) * spacing
            yy = 170 + j * 55
            if yy > 580: break
            a1 = fy + (30 if ty > fy else -30)
            a2 = ty - (30 if ty > fy else -30)
            parts.append(
                f'<g style="animation-delay:{delays[min(j + 1, len(delays) - 1)]}s">'
                f'<line x1="{a1}" y1="{yy}" x2="{a2}" y2="{yy}"'
                f' stroke="{s.arrow_color}" stroke-width="2" marker-end="url(#a_sd)"/>'
                + (f'<text x="{(a1 + a2) // 2}" y="{yy - 8}" font-size="14" fill="{s.text_color}88"'
                   f' text-anchor="middle">{cls._html_esc(label[:25])}</text>' if label else "")
                + '</g>')
        # marker
        parts.insert(1,
            '<defs>'
            f'<marker id="a_sd" viewBox="0 0 10 10" refX="10" refY="5"'
            f' markerWidth="8" markerHeight="8" orient="auto">'
            f'<path d="M0,0L10,5L0,10Z" fill="{s.arrow_color}"/></marker>'
            f'<filter id="ds_sd"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/></filter>'
            '</defs>')
        parts.append('</svg>')
        return "\n".join(parts)

    # ── 流程图 ──────────────────────────────────────────

    @classmethod
    def _flow_chart(cls, items: list[str], title: str, s: DiagramStyle,
                    w: int, h: int) -> str:
        """流程图：判断/分支/循环。"""
        delays = cls._staggered(range(10), 0, s.stagger_delay)
        parts = [cls._svg_frame(w, h)]
        if title:
            parts.append(
                f'<text x="{w // 2}" y="50" font-size="{s.title_font_size}"'
                f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
                f'{cls._html_esc(title[:60])}</text>')
        # 解析节点
        nodes_raw = items[0].split(";") if items else []
        nodes = []
        for nr in nodes_raw[:8]:
            segs = nr.split(":")
            nodes.append({
                "id": segs[0], "x": int(segs[1]) if len(segs) > 1 and segs[1].strip().isdigit() else w // 2,
                "y": int(segs[2]) if len(segs) > 2 and segs[2].strip().isdigit() else 200 + len(nodes) * 120,
                "label": segs[3] if len(segs) > 3 else segs[0], "shape": segs[4] if len(segs) > 4 else "rect",
            })
        # 解析边
        edges = []
        if len(items) > 1:
            for er in items[1].split(";"):
                segs = er.split(":", 1)
                route, lbl = segs[0], (segs[1] if len(segs) > 1 else "")
                if "->" in route:
                    p = route.split("->", 1)
                    edges.append({"from": p[0].strip(), "to": p[1].strip(), "label": lbl})
        # marker
        parts.insert(1,
            '<defs>'
            f'<marker id="a_fc" viewBox="0 0 10 10" refX="10" refY="5"'
            f' markerWidth="8" markerHeight="8" orient="auto">'
            f'<path d="M0,0L10,5L0,10Z" fill="{s.arrow_color}88"/></marker>'
            f'<filter id="ds_fc"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/></filter>'
            '</defs>')
        # 绘制边
        for ei, edge in enumerate(edges):
            fn = next((n for n in nodes if n["id"] == edge["from"]), None)
            tn = next((n for n in nodes if n["id"] == edge["to"]), None)
            if not fn or not tn: continue
            x1, y1 = fn["x"], fn["y"] + (60 if fn["shape"] == "diamond" else 30)
            x2, y2 = tn["x"], tn["y"] - (60 if tn["shape"] == "diamond" else 30)
            parts.append(
                f'<g style="animation-delay:{delays[min(ei, len(delays) - 1)]}s">'
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"'
                f' stroke="{s.arrow_color}66" stroke-width="2" marker-end="url(#a_fc)"/>'
                + (f'<text x="{(x1 + x2) // 2}" y="{(y1 + y2) // 2 - 8}" font-size="14" fill="{s.text_color}88"'
                   f' text-anchor="middle">{cls._html_esc(edge["label"][:15])}</text>' if edge["label"] else "")
                + '</g>')
        # 绘制节点
        for ni, node in enumerate(nodes):
            d = delays[min(ni, len(delays) - 1)]
            if node["shape"] == "diamond":
                parts.append(
                    f'<g style="animation-delay:{d}s">'
                    f'<polygon points="{node["x"]},{node["y"] - 40} {node["x"] + 40},{node["y"]}'
                    f' {node["x"]},{node["y"] + 40} {node["x"] - 40},{node["y"]}"'
                    f' fill="{s.accent_color}22" stroke="{s.accent_color}" stroke-width="2" filter="url(#ds_fc)"/>'
                    f'<text x="{node["x"]}" y="{node["y"] + 5}" font-size="14" fill="{s.text_color}"'
                    f' text-anchor="middle">{cls._html_esc(node["label"][:8])}</text></g>')
            elif node["shape"] == "pill":
                parts.append(
                    f'<g style="animation-delay:{d}s">'
                    f'<rect x="{node["x"] - 60}" y="{node["y"] - 20}" width="120" height="40" rx="20"'
                    f' fill="{s.primary_color}33" stroke="{s.primary_color}" stroke-width="2" filter="url(#ds_fc)"/>'
                    f'<text x="{node["x"]}" y="{node["y"] + 5}" font-size="16" fill="{s.text_color}"'
                    f' text-anchor="middle">{cls._html_esc(node["label"][:12])}</text></g>')
            else:
                parts.append(
                    f'<g style="animation-delay:{d}s">'
                    f'<rect x="{node["x"] - 70}" y="{node["y"] - 25}" width="140" height="50" rx="{s.border_radius}"'
                    f' fill="{s.item_bg}" stroke="{s.primary_color}66" stroke-width="2" filter="url(#ds_fc)"/>'
                    f'<text x="{node["x"]}" y="{node["y"] + 5}" font-size="16" fill="{s.text_color}"'
                    f' text-anchor="middle">{cls._html_esc(node["label"][:15])}</text></g>')
        parts.append('</svg>')
        return "\n".join(parts)
