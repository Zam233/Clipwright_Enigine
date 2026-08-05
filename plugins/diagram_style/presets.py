"""图解视觉风格预设 — 通过 Hook 注册多组配色主题。

用法：
  在 Persona visual_config 中设置 style_preset: "gold_black"
  即可全局应用该配色风格。

插件注册方式：
  HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, register_style_presets)
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins.hooks import HookRegistry, HookPoint

# ── 内置配色主题 ─────────────────────────────────────

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    # 黑金主题 — 知识区 / 深度内容
    "gold_black": {
        "primary_color": "#d4a843",
        "secondary_color": "#8b6f2e",
        "accent_color": "#f0c860",
        "item_bg": "rgba(212,168,67,0.15)",
        "item_bg_alt": "rgba(240,200,96,0.10)",
        "text_color": "#f5e6c8",
        "arrow_color": "#d4a843",
        "vs_color": "#e8b84c",
        "bg_color": "rgba(0,0,0,0.35)",
        "stagger_delay": 0.3,
    },
    # 赛博朋克 — 数码 / 科技 / 游戏
    "cyber": {
        "primary_color": "#00f0ff",
        "secondary_color": "#ff0066",
        "accent_color": "#a855f7",
        "item_bg": "rgba(0,240,255,0.12)",
        "item_bg_alt": "rgba(255,0,102,0.10)",
        "text_color": "#e0f0ff",
        "arrow_color": "#00f0ff",
        "vs_color": "#ff0066",
        "bg_color": "rgba(0,0,0,0.40)",
        "stagger_delay": 0.2,
    },
    # 极简白 — 教程 / 文档 / 专业演示
    "minimal_white": {
        "primary_color": "#2563eb",
        "secondary_color": "#ea580c",
        "accent_color": "#0891b2",
        "item_bg": "rgba(37,99,235,0.08)",
        "item_bg_alt": "rgba(234,88,12,0.06)",
        "text_color": "#1e293b",
        "arrow_color": "#2563eb",
        "vs_color": "#ea580c",
        "bg_color": "rgba(255,255,255,0.8)",
        "stagger_delay": 0.25,
    },
    # 暖白 — 生活 / Vlog / 人文
    "warm_white": {
        "primary_color": "#b45309",
        "secondary_color": "#78716c",
        "accent_color": "#d97706",
        "item_bg": "rgba(180,83,9,0.08)",
        "item_bg_alt": "rgba(120,113,108,0.06)",
        "text_color": "#292524",
        "arrow_color": "#b45309",
        "vs_color": "#78716c",
        "bg_color": "rgba(255,252,245,0.85)",
        "stagger_delay": 0.3,
    },
    # 暗色森林 — 户外 / 自然 / 环保
    "forest_dark": {
        "primary_color": "#22c55e",
        "secondary_color": "#a3e635",
        "accent_color": "#86efac",
        "item_bg": "rgba(34,197,94,0.12)",
        "item_bg_alt": "rgba(163,230,53,0.08)",
        "text_color": "#dcfce7",
        "arrow_color": "#22c55e",
        "vs_color": "#a3e635",
        "bg_color": "rgba(0,0,0,0.35)",
        "stagger_delay": 0.25,
    },
}


def register_style_presets(context: dict) -> dict:
    """Hook 回调：注册所有配色主题。

    被 HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET) 调用。
    """
    return {"presets": STYLE_PRESETS}


# ── 自动注册 ─────────────────────────────────────────

def __init_plugin__() -> None:
    """插件加载入口。"""
    HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, register_style_presets)
    from clipwright.config import logger
    logger.info("DiagramStyle 插件已加载: %d 个配色主题", len(STYLE_PRESETS))
