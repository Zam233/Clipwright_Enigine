"""AnimationCatalog — 动态动画类型目录，供 StructureAgent / AnimationAgent 查询可用类型。

职责：
1. 从 AnimationRegistry 读取已注册的文字动画和逻辑动画
2. 支持插件通过 HookRegistry 扩展逻辑动画类型
3. 提供 StructureAgent 需要的 prompt 友好的动画类型描述
4. 提供 AnimationAgent 需要的标记 → 类型解析
5. 提供入场/出场关键帧生成
"""

from __future__ import annotations

from typing import Any

from clipwright.animation.registry import AnimationRegistry
from clipwright.config import logger
from clipwright.schema.animation import AnimationType

# 内置逻辑动画（由 DiagramSVG 渲染引擎驱动）
_BUILTIN_LOGIC_ANIMATIONS: list[dict[str, str]] = [
    {"id": "diagram", "name": "箭头", "category": "logic",
     "desc": "展示因果关系 A→B→C，逐元素入场，支持渐变阴影"},
    {"id": "causation", "name": "因果", "category": "logic",
     "desc": "因果链条 A → 导致 → B，逐元素入场"},
    {"id": "comparison", "name": "对比", "category": "logic",
     "desc": "对比两个事物 A vs B，渐变背景 + 逐元素入场"},
    {"id": "sequence", "name": "流程", "category": "logic",
     "desc": "步骤/流程/序列，编号圆圈 + 逐元素入场"},
    {"id": "timeline", "name": "时间线", "category": "logic",
     "desc": "历史演进/项目里程碑，水平时间轴 + 节点"},
    {"id": "tree", "name": "层级", "category": "logic",
     "desc": "分类/组织结构/目录树，根节点 + 子节点"},
    {"id": "venn", "name": "维恩", "category": "logic",
     "desc": "交集/包含关系，双圆重叠图"},
    {"id": "bar_chart", "name": "柱状图", "category": "logic",
     "desc": "数据对比，垂直柱状图"},
    {"id": "pie_chart", "name": "饼图", "category": "logic",
     "desc": "占比分布，扇形 + 图例"},
    {"id": "line_chart", "name": "折线图", "category": "logic",
     "desc": "趋势变化，带面积填充 + 数据点"},
]


class AnimationCatalog:
    """动画类型动态目录。"""

    @staticmethod
    def get_text_animations() -> list[dict[str, Any]]:
        """返回所有已注册的文字动画（来自 AnimationRegistry + 插件注册）。

        返回格式:
        [
            {"id": "fade_in", "name": "淡入", "desc": "普通文字出现"},
            {"id": "typewriter", "name": "打字", "desc": "逐字出现"},
            ...
        ]
        """
        defs = AnimationRegistry.list(AnimationType.TEXT)
        result = []
        for d in defs:
            result.append({
                "id": d.animation_id,
                "name": d.name or d.animation_id,
                "desc": d.description or "",
            })
        if not result:
            # 默认文字动画（当 AnimationRegistry 未填充时）
            result = [
                {"id": "text_fade_in", "name": "淡入", "desc": "文字从透明到不透明"},
                {"id": "typewriter", "name": "打字", "desc": "逐字出现，适合引言或关键结论"},
                {"id": "text_slide_up", "name": "滑入", "desc": "文字从下方滑入"},
                {"id": "char_by_char", "name": "逐字", "desc": "每个字符依次出现"},
                {"id": "highlight_flash", "name": "高亮", "desc": "高亮闪烁强调"},
                {"id": "slide_down", "name": "下滑", "desc": "文字从上方滑入"},
                {"id": "slide_left", "name": "左滑", "desc": "文字从右侧滑入"},
                {"id": "slide_right", "name": "右滑", "desc": "文字从左侧滑入"},
                {"id": "zoom_in", "name": "放大", "desc": "文字从中心放大出现"},
                {"id": "scale_bounce", "name": "弹跳", "desc": "带弹性的缩放入场"},
                {"id": "rotate_in", "name": "旋转", "desc": "旋转进入"},
                {"id": "blur_in", "name": "模糊", "desc": "从模糊到清晰"},
                {"id": "shake", "name": "震动", "desc": "抖动效果"},
                {"id": "pulse", "name": "脉冲", "desc": "呼吸式闪烁"},
            ]
        return result

    @staticmethod
    def get_logic_animations() -> list[dict[str, Any]]:
        """返回所有可用的逻辑动画（内置 + 插件扩展 + MG 动画）。"""
        result = list(_BUILTIN_LOGIC_ANIMATIONS)

        # 合并 MG 动画（MGRenderer 从内置 clipwright/animation/mg/templates/ 加载）
        try:
            from clipwright.animation.mg_renderer import MGRenderer
            for mg in MGRenderer.list_animations():
                if not any(r["id"] == mg["id"] for r in result):
                    result.append({
                        "id": mg["id"],
                        "name": mg["name"],
                        "category": "logic",
                        "desc": mg.get("description", ""),
                    })
        except Exception:
            pass

        # 合并 DiagramSVG 支持的图解类型（内置 + 插件注册）
        try:
            from clipwright.animation.diagram_svg import DiagramRenderer
            for p in DiagramRenderer.get_supported_presets():
                pid = p["id"]
                if not any(r["id"] == pid for r in result):
                    result.append({
                        "id": pid,
                        "name": p["name"],
                        "category": "logic",
                        "desc": p["desc"],
                    })
        except Exception:
            pass

        # 尝试从插件扩展逻辑动画类型（老接口，兼容）
        try:
            from clipwright.plugins.hooks import HookRegistry, HookPoint
            ctx = HookRegistry.execute(HookPoint.ANIMATION_CATALOG_EXTEND, {})
            extensions = ctx.get("extensions", [])
            for ext in extensions:
                if isinstance(ext, dict) and "id" in ext:
                    pid = ext["id"]
                    if not any(r["id"] == pid for r in result):
                        result.append({
                            "id": pid,
                            "name": ext.get("name", pid),
                            "category": ext.get("category", "logic"),
                            "desc": ext.get("desc", ""),
                        })
        except Exception:
            pass

        return result

    @staticmethod
    def get_transition_animations() -> list[dict[str, Any]]:
        """返回所有已注册的过渡动画（来自 AnimationRegistry TRANSITION 类型）。"""
        from clipwright.schema.animation import AnimationType
        defs = AnimationRegistry.list(AnimationType.TRANSITION)
        return [
            {"id": d.animation_id, "name": d.name or d.animation_id,
             "desc": d.description or "", "duration_sec": d.duration_sec}
            for d in defs
        ]

    @staticmethod
    def get_text_animations_prompt() -> str:
        """生成 StructureAgent 用的文字动画引导文本。"""
        anims = AnimationCatalog.get_text_animations()
        if not anims:
            return ""
        lines = ["## 文字动画标记（作用于文字轨 clip 的入场/出场）"]
        for a in anims:
            name = a.get("name", a["id"])
            desc = a.get("desc", "")
            lines.append(f"  [文字动画]{name} — {desc}")
        lines.append('格式：在场景 description 中写 [文字动画]动画名：要显示的文字')
        lines.append('示例：description: "以中立风格引入话题 [文字动画]淡入：人工智能正在改变世界"')
        return "\n".join(lines)

    @staticmethod
    def get_logic_animations_prompt() -> str:
        """生成 StructureAgent 用的逻辑动画引导文本。"""
        anims = AnimationCatalog.get_logic_animations()
        if not anims:
            return ""
        lines = ["## 逻辑动画标记（独立创建动画轨，展示逻辑关系）"]
        for a in anims:
            name = a.get("name", a["id"])
            desc = a.get("desc", "")
            lines.append(f"  [逻辑动画]{name} — {desc}")
        lines.append('格式：在场景 description 中写 [逻辑动画]动画名：要展示的概念')
        lines.append('示例：description: "技术发展演进 [逻辑动画]箭头：机器学习→深度学习→强化学习"')
        return "\n".join(lines)

    @staticmethod
    def resolve_marker(marker_text: str) -> dict[str, Any]:
        """将标记文字解析为 {type, anim_id, name}。

        支持模糊匹配：精确 name → 包含匹配 → id 匹配。
        """
        # 0. 特殊标记优先匹配 — mg_dynamic 触发 LLM 动态 MG 引擎
        if marker_text == "mg_dynamic":
            return {"type": "logic", "anim_id": "mg_dynamic", "name": "LLM 动态 MG"}

        # 1. 精确 name 匹配
        for a in AnimationCatalog.get_text_animations():
            if a["name"] == marker_text:
                return {"type": "text", "anim_id": a["id"], "name": a["name"]}
        for a in AnimationCatalog.get_logic_animations():
            if a["name"] == marker_text:
                return {"type": "logic", "anim_id": a["id"], "name": a["name"]}

        # 2. 包含匹配（处理 LLM 加括号/后缀的情况如 "淡入(发光)"）
        for a in AnimationCatalog.get_text_animations():
            if a["name"] in marker_text or marker_text in a["name"]:
                return {"type": "text", "anim_id": a["id"], "name": a["name"]}
        for a in AnimationCatalog.get_logic_animations():
            if a["name"] in marker_text or marker_text in a["name"]:
                return {"type": "logic", "anim_id": a["id"], "name": a["name"]}

        # 3. id 匹配
        for a in AnimationCatalog.get_text_animations():
            if a["id"] == marker_text:
                return {"type": "text", "anim_id": a["id"], "name": a["name"]}
        for a in AnimationCatalog.get_logic_animations():
            if a["id"] == marker_text:
                return {"type": "logic", "anim_id": a["id"], "name": a["name"]}

        # 默认回退
        logger.warning("AnimationCatalog: 未匹配标记 '%s', 回退到淡入", marker_text)
        return {"type": "text", "anim_id": "text_fade_in", "name": "淡入"}

    @staticmethod
    def parse_marker_from_description(desc: str) -> list[dict[str, Any]]:
        """从场景描述中解析所有的动画标记。

        支持格式:
        - [文字动画]淡入：要显示的文字
        - [文字动画]打字 — 引言
        - [逻辑动画]箭头：A→B→C
        - [动画]淡入（向后兼容）

        Returns:
            [{"type": "text", "anim_id": "text_fade_in", "name": "淡入",
              "text": "要显示的文字", "full_match": "[文字动画]淡入：..."}, ...]
        """
        import re
        results = []

        def _extract(match: re.Match, forced_type: str = "") -> dict | None:
            """从正则匹配中提取动画标记信息。"""
            name = match.group(1)
            after = desc[match.end():].strip()
            # 提取文字内容（冒号/em dash 后面的就是文字）
            text = ""
            for sep in re.findall(r'^[：:—\-]\s*(.*)', after):
                text = sep.strip()
                break
            if not text:
                # 尝试取到第一个标点或句号
                text_match = re.match(r'^(\S+)\s*', after)
                if text_match:
                    text = text_match.group(1)

            # JSON payload 提取：structure_agent 会写入
            # [逻辑动画]mg_dynamic:{"description":"...","text":"A|B","style":"..."}
            # 此时 text 是整个 JSON 串，必须解析出结构化字段，
            # 否则 JSON 片段会被当作屏上大字渲染。
            if text.strip().startswith("{"):
                import json as _json
                try:
                    payload = _json.loads(text.strip())
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    info = AnimationCatalog.resolve_marker(name)
                    if forced_type:
                        info["type"] = forced_type
                    payload_text = payload.get("text", "") or ""
                    payload_desc = payload.get("description", "") or ""
                    if payload_text:
                        info["text"] = payload_text
                    elif payload_desc:
                        # 无 text 字段 → 回退到 description（截断），绝不保留原始 JSON
                        info["text"] = payload_desc[:50]
                    info["description"] = payload_desc
                    info["style"] = payload.get("style", "") or ""
                    info["payload"] = payload
                    info["full_match"] = match.group(0)
                    return info

            info = AnimationCatalog.resolve_marker(name)
            if forced_type:
                info["type"] = forced_type
            if text:
                info["text"] = text
            info["full_match"] = match.group(0)
            return info

        # 1. [文字动画]xxx
        # 注意：动画名后面紧跟 ：或 — 或空白表示文字内容开始
        for m in re.finditer(r'\[文字动画\]([^：:—\s]+)', desc):
            info = _extract(m, "text")
            if info:
                results.append(info)

        # 2. [逻辑动画]xxx
        for m in re.finditer(r'\[逻辑动画\]([^：:—\s]+)', desc):
            info = _extract(m, "logic")
            if info:
                results.append(info)

        # 3. [动画]xxx（向后兼容）
        if not results:
            for m in re.finditer(r'\[动画\]([^：:—\s]+)', desc):
                info = _extract(m)
                if info:
                    results.append(info)

        return results

    # ── 入场关键帧定义 ──────────────────────────────────

    _ENTRANCE_KEYFRAMES: dict[str, list[dict]] = {
        "text_fade_in": [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 0.4, "properties": {"opacity": 1}},
        ],
        "text_slide_up": [
            {"time": 0, "properties": {"opacity": 0, "translate_y": 30}},
            {"time": 0.5, "properties": {"opacity": 1, "translate_y": 0}},
        ],
        "slide_down": [
            {"time": 0, "properties": {"opacity": 0, "translate_y": -30}},
            {"time": 0.5, "properties": {"opacity": 1, "translate_y": 0}},
        ],
        "slide_left": [
            {"time": 0, "properties": {"opacity": 0, "translate_x": 50}},
            {"time": 0.5, "properties": {"opacity": 1, "translate_x": 0}},
        ],
        "slide_right": [
            {"time": 0, "properties": {"opacity": 0, "translate_x": -50}},
            {"time": 0.5, "properties": {"opacity": 1, "translate_x": 0}},
        ],
        "typewriter": [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 0.01, "properties": {"opacity": 1}},
        ],
        "char_by_char": [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 0.01, "properties": {"opacity": 1}},
        ],
        "highlight_flash": [
            {"time": 0, "properties": {"opacity": 1}},
            {"time": 0.1, "properties": {"opacity": 0.4}},
            {"time": 0.2, "properties": {"opacity": 1}},
            {"time": 0.3, "properties": {"opacity": 0.4}},
            {"time": 0.4, "properties": {"opacity": 1}},
        ],
        "shake": [
            {"time": 0, "properties": {"opacity": 1, "translate_x": 0}},
            {"time": 0.08, "properties": {"translate_x": -4}},
            {"time": 0.16, "properties": {"translate_x": 4}},
            {"time": 0.24, "properties": {"translate_x": -2}},
            {"time": 0.32, "properties": {"opacity": 1, "translate_x": 0}},
        ],
        "scale_bounce": [
            {"time": 0, "properties": {"opacity": 0, "scale_x": 0.0, "scale_y": 0.0}},
            {"time": 0.5, "properties": {"opacity": 1, "scale_x": 1.15, "scale_y": 1.15}},
            {"time": 0.8, "properties": {"opacity": 1, "scale_x": 0.95, "scale_y": 0.95}},
            {"time": 1.0, "properties": {"opacity": 1, "scale_x": 1.0, "scale_y": 1.0}},
        ],
        "blur_in": [
            {"time": 0, "properties": {"opacity": 0, "blur": 12}},
            {"time": 0.3, "properties": {"opacity": 0, "blur": 12}},
            {"time": 0.8, "properties": {"opacity": 1, "blur": 0}},
        ],
        "rotate_in": [
            {"time": 0, "properties": {"opacity": 0, "rotate": -45}},
            {"time": 0.3, "properties": {"opacity": 1, "rotate": 5}},
            {"time": 0.5, "properties": {"opacity": 1, "rotate": 0}},
        ],
        "pulse": [
            {"time": 0, "properties": {"opacity": 1, "scale_x": 1.0, "scale_y": 1.0}},
            {"time": 0.25, "properties": {"scale_x": 1.2, "scale_y": 1.2, "opacity": 0.8}},
            {"time": 0.5, "properties": {"scale_x": 1.0, "scale_y": 1.0, "opacity": 1}},
            {"time": 0.75, "properties": {"scale_x": 1.2, "scale_y": 1.2, "opacity": 0.8}},
            {"time": 1.0, "properties": {"scale_x": 1.0, "scale_y": 1.0, "opacity": 1}},
        ],
    }

    # drawtext 无法模拟的属性列表（scale_x/scale_y 被 render 转为 fontsize 表达式，仍可工作）
    _DRAWTEXT_UNSUPPORTED_PROPS = {"rotate", "blur"}

    # ── CSS 动画定义（用于 Hyperframes HTML 渲染） ─────────

    _CSS_ANIMATIONS: dict[str, dict] = {
        "text_fade_in": {
            "class": "hf-fade-in",
            "keyframes": "@keyframes hf-fade-in { 0% { opacity: 0; } 100% { opacity: 1; } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "fade_in": {
            "class": "hf-fade-in",
            "keyframes": "@keyframes hf-fade-in { 0% { opacity: 0; } 100% { opacity: 1; } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "typewriter": {
            "class": "hf-typewriter",
            "keyframes": "@keyframes hf-typewriter { 0% { clip-path: inset(0 100% 0 0); } 100% { clip-path: inset(0 0 0 0); } }",
            "duration": 1.5, "easing": "ease-out",
        },
        "char_by_char": {
            "class": "hf-typewriter",
            "keyframes": "@keyframes hf-typewriter { 0% { clip-path: inset(0 100% 0 0); } 100% { clip-path: inset(0 0 0 0); } }",
            "duration": 1.5, "easing": "ease-out",
        },
        "text_slide_up": {
            "class": "hf-slide-up",
            "keyframes": "@keyframes hf-slide-up { 0% { opacity: 0; transform: translateY(30px); } 100% { opacity: 1; transform: translateY(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "slide_up": {
            "class": "hf-slide-up",
            "keyframes": "@keyframes hf-slide-up { 0% { opacity: 0; transform: translateY(30px); } 100% { opacity: 1; transform: translateY(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "slide_down": {
            "class": "hf-slide-down",
            "keyframes": "@keyframes hf-slide-down { 0% { opacity: 0; transform: translateY(-30px); } 100% { opacity: 1; transform: translateY(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "slide_left": {
            "class": "hf-slide-left",
            "keyframes": "@keyframes hf-slide-left { 0% { opacity: 0; transform: translateX(50px); } 100% { opacity: 1; transform: translateX(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "slide_right": {
            "class": "hf-slide-right",
            "keyframes": "@keyframes hf-slide-right { 0% { opacity: 0; transform: translateX(-50px); } 100% { opacity: 1; transform: translateX(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "zoom_in": {
            "class": "hf-zoom-in",
            "keyframes": "@keyframes hf-zoom-in { 0% { opacity: 0; transform: scale(0.5); } 100% { opacity: 1; transform: scale(1); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "scale_bounce": {
            "class": "hf-scale-bounce",
            "keyframes": "@keyframes hf-scale-bounce { 0% { opacity: 0; transform: scale(0); } 50% { opacity: 1; transform: scale(1.15); } 80% { transform: scale(0.95); } 100% { opacity: 1; transform: scale(1); } }",
            "duration": 1.0, "easing": "ease-out",
        },
        "rotate_in": {
            "class": "hf-rotate-in",
            "keyframes": "@keyframes hf-rotate-in { 0% { opacity: 0; transform: rotate(-45deg); } 100% { opacity: 1; transform: rotate(0); } }",
            "duration": 0.5, "easing": "ease-out",
        },
        "blur_in": {
            "class": "hf-blur-in",
            "keyframes": "@keyframes hf-blur-in { 0% { filter: blur(12px); opacity: 0; } 30% { filter: blur(12px); opacity: 0; } 100% { filter: blur(0); opacity: 1; } }",
            "duration": 0.8, "easing": "ease-out",
        },
        "shake": {
            "class": "hf-shake",
            "keyframes": "@keyframes hf-shake { 0%, 100% { transform: translateX(0); } 20% { transform: translateX(-4px); } 40% { transform: translateX(4px); } 60% { transform: translateX(-2px); } 80% { transform: translateX(0); } }",
            "duration": 0.4, "easing": "ease-in-out",
        },
        "pulse": {
            "class": "hf-pulse",
            "keyframes": "@keyframes hf-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.8; transform: scale(1.2); } }",
            "duration": 0.6, "easing": "ease-in-out",
        },
        "highlight_flash": {
            "class": "hf-glowing",
            "keyframes": "@keyframes hf-glowing { 0%, 100% { filter: brightness(1); } 50% { filter: brightness(1.5); } }",
            "duration": 0.4, "easing": "ease-in-out",
        },
        "glow": {
            "class": "hf-glowing",
            "keyframes": "@keyframes hf-glowing { 0%, 100% { filter: brightness(1); } 50% { filter: brightness(1.5); } }",
            "duration": 0.6, "easing": "ease-in-out",
        },
    }

    _CSS_EXIT_KEYFRAMES = (
        "@keyframes hf-fade-out {"
        "  0% { opacity: 1; }"
        "  100% { opacity: 0; }"
        "}"
        "@keyframes hf-diagram-reveal {"
        "  0% { visibility: hidden; }"
        "  100% { visibility: visible; }"
        "}"
    )

    @staticmethod
    def get_css_animation(anim_id: str) -> dict:
        """返回 CSS 动画定义，找不到时回退到淡入。"""
        return AnimationCatalog._CSS_ANIMATIONS.get(
            anim_id,
            AnimationCatalog._CSS_ANIMATIONS.get("text_fade_in", {
                "class": "hf-fade-in",
                "keyframes": "",
                "duration": 0.5, "easing": "ease-out",
            }),
        )

    @staticmethod
    def get_css_keyframes_all() -> str:
        """返回所有 CSS @keyframes 定义 + 出口淡出。"""
        seen = set()
        parts = [AnimationCatalog._CSS_EXIT_KEYFRAMES]
        for anim in AnimationCatalog._CSS_ANIMATIONS.values():
            kfs = anim.get("keyframes", "")
            if kfs and kfs not in seen:
                parts.append(kfs)
                seen.add(kfs)
        return "\n".join(parts)

    @staticmethod
    def build_full_keyframes(
        anim_id: str, clip_start: float, clip_duration: float
    ) -> list[dict]:
        """生成完整的关键帧序列：入场 + 保持 + 出场（淡出）。

        返回的 keyframe 时间均为绝对时间（视频时间轴）。

        Args:
            anim_id: 动画 ID（如 "text_fade_in"）
            clip_start: clip 在时间轴上的起始时间（秒）
            clip_duration: clip 持续时间（秒）

        Returns:
            [{"time": sec, "properties": {prop: val}}, ...]
        """
        entrance = AnimationCatalog._ENTRANCE_KEYFRAMES.get(anim_id, [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 0.4, "properties": {"opacity": 1}},
        ])

        # 入场结束时间
        entrance_end = max(kf["time"] for kf in entrance) if entrance else 0.5
        entrance_end = max(entrance_end, 0.3)

        # 出场时长
        exit_dur = min(0.3, clip_duration * 0.15)
        exit_dur = max(exit_dur, 0.15)

        full: list[dict] = []

        # 1. 入场阶段（时间相对 clip_start 偏移）
        for kf in entrance:
            full.append({
                "time": round(clip_start + kf["time"], 2),
                "properties": dict(kf["properties"]),
            })

        # 2. 保持阶段（入场结束 → 出场开始）
        # 入场最后一个 keyframe 的时间即入场结束时间
        entrance_end_abs = clip_start + entrance_end
        exit_start = clip_start + clip_duration - exit_dur

        hold_props = dict(entrance[-1]["properties"])
        hold_props["opacity"] = 1

        # 如果入场结束 < 出场开始，且有足够间隔，添加保持段
        # 注意：entrance 最后一个 keyframe 已经覆盖了 entrance_end_abs 时刻
        # 所以如果入场结束 == 出场开始，不需要额外 hold keyframes
        if exit_start > entrance_end_abs + 0.1:
            full.append({"time": round(exit_start, 2), "properties": hold_props})

        # 3. 出场（淡出）
        exit_end_props = dict(entrance[-1]["properties"])
        exit_end_props["opacity"] = 0

        full.append({"time": round(clip_start + clip_duration, 2), "properties": exit_end_props})

        return full

    @staticmethod
    def get_unsupported_properties(anim_id: str) -> set[str]:
        """返回该动画 ID 中 drawtext 不支持的属性列表。"""
        entrance = AnimationCatalog._ENTRANCE_KEYFRAMES.get(anim_id, [])
        all_props = set()
        for kf in entrance:
            all_props.update(kf.get("properties", {}).keys())
        unsupported = all_props & AnimationCatalog._DRAWTEXT_UNSUPPORTED_PROPS
        return unsupported

    @staticmethod
    def get_entrance_duration(anim_id: str) -> float:
        """返回动画的入场持续时间（秒）。"""
        entrance = AnimationCatalog._ENTRANCE_KEYFRAMES.get(anim_id, [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 0.4, "properties": {"opacity": 1}},
        ])
        return max(kf["time"] for kf in entrance) if entrance else 0.5

    @staticmethod
    def resolve_persona_style(
        visual_config: dict | None, extra_params: dict | None = None
    ) -> dict[str, Any]:
        """从 Persona 视觉配置中解析文字样式参数。

        Returns:
            {"font_size": int, "font_color": str, "position": str}
        """
        config = visual_config or extra_params or {}
        if extra_params and not config:
            config = extra_params.get("visual_config", {})

        return {
            "font_size": config.get("text_font_size", 48),
            "font_color": config.get("text_color", "#ffffff"),
            "position": config.get("text_position", "bottom"),
            "stroke_width": config.get("text_stroke_width", 1),
            "stroke_color": config.get("text_stroke_color", "#000000"),
            "bg_opacity": config.get("text_bg_opacity", 0.0),
        }
