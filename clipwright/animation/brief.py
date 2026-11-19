"""Animation brief + style library (garden-skills quality plan)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

DEFAULT_FORBIDDEN = [
    "默认深色背景 + 居中大字 + 紫蓝渐变 + 到处发光",
    "所有元素同时淡入",
    "无目的粒子/光斑/光晕",
    "匀速平移作为主运动",
    "主体过小、机位完全不动",
    "一屏长句或满屏小字",
]


@dataclass(frozen=True)
class AnimationStyle:
    id: str
    name: str
    signals: tuple[str, ...]
    palette: tuple[str, ...]
    display_type: str
    motion_character: str
    easing: str
    entry_sec: float
    exit_sec: float
    stagger_sec: float
    forbidden: tuple[str, ...] = ()


STYLE_LIBRARY: dict[str, AnimationStyle] = {
    "swiss-editorial": AnimationStyle(
        "swiss-editorial", "瑞士编辑",
        ("左对齐网格", "大留白", "单色强调", "巨大字号"),
        ("#F7F3EA", "#111111", "#D7263D"),
        "heavy grotesque", "snap / 硬切 / 无回弹", "snap", 0.35, 0.20, 0.04,
        ("居中大标题", "紫色渐变", "发光粒子"),
    ),
    "cinematic-title": AnimationStyle(
        "cinematic-title", "电影标题",
        ("大留白", "慢推", "强字重", "单点光"),
        ("#0B0C0F", "#F5F1E8", "#C8A96A"),
        "high-contrast serif/sans", "slow glide / 长镜头", "glide", 0.55, 0.30, 0.08,
        ("满屏粒子", "快速弹跳", "多色渐变"),
    ),
    "soft-3d": AnimationStyle(
        "soft-3d", "柔和 3D",
        ("柔和体积", "低对比", "圆角", "轻阴影"),
        ("#F4F1EA", "#D8CEC0", "#6E8BFF"),
        "rounded sans", "spring / 轻回弹", "back-out", 0.5, 0.25, 0.06,
        ("硬边黑色", "高速抖动"),
    ),
    "blueprint": AnimationStyle(
        "blueprint", "蓝图工程",
        ("线框", "网格", "标注", "单色"),
        ("#0E2238", "#E8F0F7", "#4FB3FF"),
        "technical mono/sans", "precise / 线性网格", "ease-out", 0.4, 0.2, 0.05,
        ("发光渐变", "有机漂浮"),
    ),
    "paper-ink": AnimationStyle(
        "paper-ink", "纸墨",
        ("纸色", "墨线", "手写", "扫描质感"),
        ("#F4EEDF", "#1B1A17", "#B23A2A"),
        "serif + hand", "organic / 轻不规则", "ease-in-out", 0.5, 0.25, 0.08,
        ("霓虹", "金属渐变"),
    ),
    "data-story": AnimationStyle(
        "data-story", "数据故事",
        ("结论先行", "大数字", "图表", "单色强调"),
        ("#F6F7F9", "#12161C", "#00A8E8"),
        "neutral sans + tabular", "build / count-up / hold", "ease-out", 0.45, 0.22, 0.05,
        ("无标签图表", "同时滚动多个数字"),
    ),
    "comic-panel": AnimationStyle(
        "comic-panel", "漫画分格",
        ("分格", "硬切", "拟声", "高对比"),
        ("#FFF8E7", "#111111", "#FFD400"),
        "bold display", "snap / 冲击 / 停顿", "snap", 0.3, 0.15, 0.03,
        ("柔和渐变", "匀速漂浮"),
    ),
    "loop-minimal": AnimationStyle(
        "loop-minimal", "极简循环",
        ("固定机位", "首尾同帧", "少量元素", "精确速度"),
        ("#EFEFEF", "#202020", "#FF5A36"),
        "geometric sans", "loop / 匀速但不空转", "linear", 0.4, 0.2, 0.05,
        ("不可循环的结尾", "随机粒子"),
    ),
}


@dataclass
class AnimationBrief:
    topic: str
    audience: str = ""
    platform: str = ""
    duration_sec: float = 8.0
    aspect: str = "16:9"
    style_id: str = "swiss-editorial"
    benchmark: dict[str, Any] = field(default_factory=dict)
    structure: list[str] = field(default_factory=lambda: ["Hook", "建立", "签名", "落版"])
    takeaway: str = ""
    signature: str = ""
    palette: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pick_style_id(description: str, category_context: dict[str, Any] | None = None) -> str:
    text = f"{description} {(category_context or {}).get('display_name', '')} {(category_context or {}).get('description', '')}".lower()
    if any(k in text for k in ("数据", "chart", "增长", "stat")):
        return "data-story"
    if any(k in text for k in ("电影", "预告", "cinematic")):
        return "cinematic-title"
    if any(k in text for k in ("漫画", "comic", "分格")):
        return "comic-panel"
    if any(k in text for k in ("工程", "蓝图", "流程", "blueprint")):
        return "blueprint"
    if any(k in text for k in ("循环", "loop", "极简")):
        return "loop-minimal"
    return "swiss-editorial"


def build_brief(
    description: str, category_context: dict[str, Any] | None = None,
    duration_sec: float = 8.0, style_id: str | None = None,
) -> AnimationBrief:
    sid = style_id or pick_style_id(description, category_context)
    style = STYLE_LIBRARY.get(sid) or STYLE_LIBRARY["swiss-editorial"]
    return AnimationBrief(
        topic=description[:160],
        duration_sec=max(1.0, float(duration_sec or 8.0)),
        style_id=style.id,
        benchmark={"reference": "", "learn": [], "avoid": []},
        takeaway=description[:80],
        signature="一个主运动 + 一个记忆点",
        palette=list(style.palette),
        forbidden=list(dict.fromkeys(list(DEFAULT_FORBIDDEN) + list(style.forbidden))),
    )


def validate_brief(brief: AnimationBrief) -> list[str]:
    errors: list[str] = []
    if not brief.topic.strip():
        errors.append("topic is required")
    if brief.duration_sec <= 0:
        errors.append("duration_sec must be positive")
    if brief.style_id not in STYLE_LIBRARY:
        errors.append("unknown style_id")
    if len(brief.palette) < 2:
        errors.append("palette must have at least 2 colors")
    if not brief.signature.strip():
        errors.append("signature is required")
    return errors


def style_prompt_block(brief: AnimationBrief) -> str:
    style = STYLE_LIBRARY.get(brief.style_id) or STYLE_LIBRARY["swiss-editorial"]
    return (
        "## Brief / 风格帧\n"
        f"- topic: {brief.topic}\n"
        f"- style: {style.id} / {style.name}\n"
        f"- signals: {', '.join(style.signals)}\n"
        f"- palette (must 2+1): {', '.join(brief.palette[:3])}\n"
        f"- type: {style.display_type}\n"
        f"- motion: {style.motion_character}; easing={style.easing}; entry={style.entry_sec}s; exit={style.exit_sec}s; stagger={style.stagger_sec}s\n"
        f"- takeaway: {brief.takeaway}\n"
        f"- signature: {brief.signature}\n"
        f"- forbidden: {'; '.join(brief.forbidden)}\n"
    )
