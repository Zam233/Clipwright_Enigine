"""自定义风格插件 — 演示 StyleInterpreterPlugin 的实现。

该插件根据 Persona 的 identity.tone 和 visual.palette 动态生成图解风格。
创作者可在此实现任意配色逻辑，完全接管内置 LLM 解释器。

Persona 配置示例:
```yaml
visual:
  palette: "冷色调，蓝色为主"
  font: "无衬线"
  style_description: "黑底白字，蓝色强调色，大间距，适合科技内容"
  primary_color: "#3b82f6"   ← 可选精确覆盖
```
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins.base import StyleInterpreterPlugin
from clipwright.services.style_interpreter import StyleInterpreter


class CustomStylePlugin(StyleInterpreterPlugin):
    """完全自定义的风格解释器插件。

    创作者可以在这里实现任意风格逻辑，例如：
    - 根据 tone 关键字选择色板
    - 读取 visual.palette 自然语言描述 → 映射到色值
    - 根据 identity.knowledge 领域选择字体
    - 调用外部 API 获取设计师配色方案
    """

    def initialize(self) -> None:
        """注册到 StyleInterpreter。"""
        StyleInterpreter.register_plugin(self)

    async def interpret(
        self,
        visual_config: dict[str, Any],
        persona_context: dict[str, Any],
    ) -> dict[str, Any]:
        """根据 Persona 语境生成图解样式。

        这是一个完全自定义的解释器，不依赖内置 LLM。
        创作者可完全改写此方法。
        """
        identity = persona_context.get("identity", {})
        tone = identity.get("tone", "neutral")
        palette_desc = (visual_config.get("palette") or "").lower()
        font_desc = (visual_config.get("font") or "").lower()

        # ── 1. 根据 tone 选择基础色板 ──
        if any(w in tone for w in ["批判", "冷峻", "理性", "科技"]):
            base = {
                "primary_color": "#2563eb",
                "secondary_color": "#475569",
                "accent_color": "#f59e0b",
                "text_color": "#f1f5f9",
                "font_size": 26,
                "title_font_size": 34,
                "stagger_delay": 0.3,
                "font": "sans-serif",
            }
        elif any(w in tone for w in ["热情", "吐槽", "轻松", "温暖"]):
            base = {
                "primary_color": "#ea580c",
                "secondary_color": "#f97316",
                "accent_color": "#fbbf24",
                "text_color": "#fff7ed",
                "font_size": 30,
                "title_font_size": 38,
                "stagger_delay": 0.2,
                "font": "sans-serif",
            }
        elif any(w in tone for w in ["学术", "深度"]):
            base = {
                "primary_color": "#1e3a5f",
                "secondary_color": "#4a5568",
                "accent_color": "#c5a55a",
                "text_color": "#e2e8f0",
                "font_size": 26,
                "title_font_size": 34,
                "stagger_delay": 0.3,
                "font": "serif",
            }
        else:
            base = {
                "primary_color": "#4f8cff",
                "secondary_color": "#ff6b6b",
                "accent_color": "#fbbf24",
                "text_color": "#ffffff",
                "font_size": 28,
                "title_font_size": 36,
                "stagger_delay": 0.25,
                "font": "sans-serif",
            }

        # ── 2. 根据 palette 描述微调配色 ──
        if "冷" in palette_desc or "蓝" in palette_desc:
            base["primary_color"] = "#3b82f6"
            base["secondary_color"] = "#6366f1"
        elif "暖" in palette_desc or "橙" in palette_desc:
            base["primary_color"] = "#ea580c"
            base["secondary_color"] = "#d97706"
        elif "黑" in palette_desc or "暗" in palette_desc:
            base["text_color"] = "#e2e8f0"
        elif "白" in palette_desc or "亮" in palette_desc:
            base["text_color"] = "#1e293b"
            base["bg_color"] = "rgba(255,255,255,0.85)"

        # ── 3. 根据 font 描述选择字体 ──
        if "黑体" in font_desc or "无衬" in font_desc:
            base["font"] = "sans-serif"
            base["font_size"] = max(base["font_size"], 28)
        elif "宋体" in font_desc or "衬" in font_desc:
            base["font"] = "serif"
        elif "粗" in font_desc:
            base["font_size"] = base.get("font_size", 28) + 4

        return base
