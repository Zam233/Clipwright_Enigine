"""Animation prompt composer and plan/rule registry.

Borrows the *method* of HyperFrames skills (atomic rules, motion-doctrine,
cut-the-curve) without importing its runtime. The renderer remains our
own structured `mg_def` pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from clipwright.prompts.engine.animation_prompt import (
    ANIMATION_CONTINUITY_CHECK_ITEMS,
    ANIMATION_EXAMPLE,
    ANIMATION_MOTION_CONTRACT,
    ANIMATION_PROMPT_BASE,
    ANIMATION_RENDERER_CAPABILITIES,
    ANIMATION_RULE_SELECTION,
    ANIMATION_SELF_CHECK,
    ANIMATION_STYLE_CONTRACT,
)
from clipwright.prompts.mg import MG_ORCHESTRATION_CONSTRAINTS, MG_STRICT_JSON_OUTPUT

ANIMATION_CATEGORIES = (
    "kinetic-type", "stat", "charts", "logo-reveal", "lower-thirds",
    "maps", "webpage", "news", "tweet", "asset-fusion", "flow", "comparison",
)


@dataclass(frozen=True)
class AnimationRule:
    id: str
    skill: str
    categories: tuple[str, ...]
    phase: str
    snippet: str
    conflicts: tuple[str, ...] = ()


RULE_CATALOG: tuple[AnimationRule, ...] = (
    AnimationRule(
        "headline-slam", "hyperframes-animation", ("kinetic-type", "lower-thirds", "news"),
        "entry",
        "标题从 1.10–1.18 倍回落到 1.0，只回弹一次；落定后 hold ≥1s，不叠加漂浮。",
        ("elastic", "bounce"),
    ),
    AnimationRule(
        "word-rise", "hyperframes-animation", ("kinetic-type", "lower-thirds"),
        "entry",
        "逐词 translate_y 18–28px + opacity 0→1，错峰 0.06–0.12s；只让一个词群成为主角。",
    ),
    AnimationRule(
        "blur-out-up", "hyperframes-animation", ("kinetic-type", "news", "webpage"),
        "exit",
        "退场用 blur(4–8px) + translate_y -12~-24px + opacity→0；退出时仍在运动，不先停稳。",
    ),
    AnimationRule(
        "count-up", "hyperframes-animation", ("stat", "charts"),
        "body",
        "数字用 0→目标值连续计数，结束时回落/停稳；不要跳变，不要多个数字同时滚动。",
    ),
    AnimationRule(
        "data-bars", "hyperframes-animation", ("charts", "comparison"),
        "body",
        "柱/条从 0 生长，按阅读顺序错峰 0.05–0.12s；保留轴标签或数值标注，方向随语义。",
    ),
    AnimationRule(
        "connector-flow", "hyperframes-animation", ("flow", "comparison", "charts"),
        "body",
        "连接线/箭头在节点之后出现，止于节点边缘、不穿入内部；方向随流程 L→R 或增长向上。",
    ),
    AnimationRule(
        "marker-highlight", "marker-highlight", ("kinetic-type", "news", "lower-thirds"),
        "emphasis",
        "关键词高亮只用一个下划线/圈选/色块；先于文字落定，随主运动同帧出现。",
    ),
    AnimationRule(
        "carrier-title", "motion-doctrine", ANIMATION_CATEGORIES,
        "seam",
        "把标题/词群作为跨 cut 的 carrier；退出与进入保持同一对象、同轴、同方向。",
    ),
    AnimationRule(
        "cut-left", "cut-the-curve", ANIMATION_CATEGORIES,
        "transition",
        "默认 current=left；cut 在运动中，入场从路径 50%+ 处接上，不用 crossfade。",
    ),
    AnimationRule(
        "hold-beat", "motion-doctrine", ANIMATION_CATEGORIES,
        "body",
        "关键信息落定后 hold ≥1s；动作与结果之间留 0.3–0.75s 静止，不塞 idle 动效。",
    ),
)


@dataclass
class AnimationPlan:
    category: str
    intent: str
    energy: Literal["low", "medium", "high"] = "medium"
    duration_sec: float = 4.0
    global_current: str = "left"
    transition_budget: int = 2
    rule_ids: list[str] = field(default_factory=list)
    carrier: str = ""
    shot_type: str = "title_reveal"
    seams: list[dict[str, Any]] = field(default_factory=list)
    shots: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category, "intent": self.intent, "energy": self.energy,
            "duration_sec": self.duration_sec, "global_current": self.global_current,
            "transition_budget": self.transition_budget, "rule_ids": self.rule_ids,
            "carrier": self.carrier, "shot_type": self.shot_type,
            "seams": self.seams,
            "shots": self.shots or [{"index": 1, "role": "main", "rule_ids": self.rule_ids}],
        }


def _pick_category(description: str, category_context: dict[str, Any]) -> str:
    text = f"{description} {category_context.get('display_name', '')} {category_context.get('description', '')}"
    if any(k in text for k in ("地图", "map", "地域", "区域")):
        return "maps"
    if any(k in text for k in ("logo", "标志", "品牌收尾")):
        return "logo-reveal"
    if any(k in text for k in ("对比", "比较", "vs", "A/B", "优劣")):
        return "comparison"
    if any(k in text for k in ("流程", "步骤", "因果", "箭头", "flow")):
        return "flow"
    if any(k in text for k in ("数据", "图表", "增长", "百分比", "统计", "柱状", "折线")):
        return "charts"
    if any(k in text for k in ("数字", "金额", "count", "指标")):
        return "stat"
    return "kinetic-type"


def infer_energy(description: str, category_context: dict[str, Any]) -> Literal["low", "medium", "high"]:
    text = f"{description} {category_context.get('pacing', '')}".lower()
    if any(k in text for k in ("快", "冲击", "高潮", "high", "燃")):
        return "high"
    if any(k in text for k in ("慢", "舒缓", "low", "安静")):
        return "low"
    return "medium"


def build_animation_plan(
    description: str, category_context: dict[str, Any] | None = None,
    duration_sec: float = 4.0,
) -> AnimationPlan:
    category_context = category_context or {}
    category = _pick_category(description, category_context)
    energy = infer_energy(description, category_context)
    candidates = [r for r in RULE_CATALOG if category in r.categories]
    selected: list[str] = []
    for rid in ("carrier-title", "hold-beat"):
        if rid not in selected:
            selected.append(rid)
    for rule in candidates:
        if len(selected) >= 3:
            break
        if rule.id not in selected:
            selected.append(rule.id)
    shot_type = {
        "charts": "data_impact", "stat": "data_impact", "comparison": "comparison",
        "flow": "flow", "maps": "flow", "logo-reveal": "title_reveal",
    }.get(category, "title_reveal")
    return AnimationPlan(
        category=category, intent=description[:160], energy=energy,
        duration_sec=max(1.0, float(duration_sec or 4.0)),
        rule_ids=selected[:3], carrier="title-word" if category != "charts" else "bar-series",
        shot_type=shot_type,
    )


def rule_by_id(rule_id: str) -> AnimationRule | None:
    return next((r for r in RULE_CATALOG if r.id == rule_id), None)


def compose_mg_system_prompt(
    plan: AnimationPlan, *, width: int | None = None, height: int | None = None,
    category_name: str = "",
) -> str:
    lines = []
    for rid in plan.rule_ids[:3]:
        rule = rule_by_id(rid)
        if rule:
            lines.append(f"- **{rule.id}**（{rule.skill}）：{rule.snippet}")
    rules = ANIMATION_RULE_SELECTION.format(rule_lines="\n".join(lines) or "- 单主角、hold、无 idle")
    style = ANIMATION_STYLE_CONTRACT.format(
        category=category_name or plan.category, energy=plan.energy, intent=plan.intent[:120], accent="{accent}",
    )
    caps = ANIMATION_RENDERER_CAPABILITIES.format(width=width or 1920, height=height or 1080)
    plan_block = (
        "## 本镜头计划\n"
        f"- category: {plan.category}\n"
        f"- shot_type: {plan.shot_type}\n"
        f"- global_current: {plan.global_current}\n"
        f"- carrier: {plan.carrier}\n"
        f"- transition_budget: {plan.transition_budget}\n"
        f"- rule_ids: {', '.join(plan.rule_ids)}\n"
    )
    body = "\n\n".join([
        ANIMATION_PROMPT_BASE, ANIMATION_MOTION_CONTRACT, plan_block, rules, style, caps,
        ANIMATION_SELF_CHECK, ANIMATION_EXAMPLE,
    ])
    return body + MG_ORCHESTRATION_CONSTRAINTS + MG_STRICT_JSON_OUTPUT


def compose_critique_check_items() -> str:
    return ANIMATION_CONTINUITY_CHECK_ITEMS


def validate_animation_plan(plan: AnimationPlan | dict[str, Any]) -> list[str]:
    """Return plan-level errors (empty = valid)."""
    data = plan.as_dict() if isinstance(plan, AnimationPlan) else dict(plan or {})
    errors: list[str] = []
    if data.get("global_current") not in ("left", "right", "up", "down", "z_forward", "z_backward"):
        errors.append("global_current must be a known vector")
    try:
        budget = int(data.get("transition_budget", 0))
    except (TypeError, ValueError):
        budget = -1
    if not 0 <= budget <= 3:
        errors.append("transition_budget must be 0..3")
    ids = list(data.get("rule_ids") or [])
    if not 2 <= len(ids) <= 3:
        errors.append("rule_ids must select 2-3 rules")
    if any(rule_by_id(rid) is None for rid in ids):
        errors.append("unknown rule_id")
    return errors


def build_timeline_plan(scenes: list[dict[str, Any]], duration_sec: float = 4.0) -> AnimationPlan:
    """Build one plan for a multi-scene animation pass.

    Each scene entry: {clip_id, description, start_sec, duration_sec, category_context}.
    The first scene owns category/current; later shots inherit the current and get
    explicit seams so the agent can inject per-shot rule sets.
    """
    if not scenes:
        return build_animation_plan("", {}, duration_sec)
    first = scenes[0]
    base = build_animation_plan(
        str(first.get("description") or ""),
        first.get("category_context") or {},
        float(first.get("duration_sec") or duration_sec or 4.0),
    )
    shots: list[dict[str, Any]] = []
    seams: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes):
        scene_plan = build_animation_plan(
            str(scene.get("description") or base.intent),
            scene.get("category_context") or {},
            float(scene.get("duration_sec") or duration_sec or 4.0),
        )
        shot = {
            "index": i + 1, "clip_id": str(scene.get("clip_id") or ""),
            "start_sec": float(scene.get("start_sec") or 0),
            "role": "hook" if i == 0 else "body",
            "description": str(scene.get("description") or scene_plan.intent),
            "duration_sec": float(scene.get("duration_sec") or duration_sec or 4.0),
            "rule_ids": scene_plan.rule_ids,
            "carrier": scene_plan.carrier,
            "category": scene_plan.category,
        }
        shots.append(shot)
        if i:
            seams.append({
                "from_shot": i, "to_shot": i + 1,
                "at_sec": shot["start_sec"], "vector": base.global_current,
                "carrier": scene_plan.carrier,
            })
    base.shots = shots
    base.seams = seams
    base.transition_budget = min(3, max(1, len(scenes) - 1))
    return base


def validate_content_coverage(mg_def: dict[str, Any], text_content: str) -> list[str]:
    """Check that every `|` segment has an element placeholder/literal.

    Catches the 04_flow failure: model puts {step3} in params but omits the
    third node from elements, so the rendered flow silently loses a step.
    """
    tokens = [t.strip() for t in str(text_content or "").split("|") if t.strip()]
    if not tokens:
        return []
    elements = mg_def.get("elements") or []
    params = mg_def.get("params") or {}
    element_text = " ".join(str(e.get("content") or "") for e in elements if isinstance(e, dict))
    errors: list[str] = []
    for token in tokens:
        keys = [
            str(k) for k, v in params.items()
            if isinstance(v, dict) and str(v.get("default", "")).strip() == token
        ]
        if keys:
            if not any("{" + k + "}" in element_text for k in keys):
                errors.append(f"内容段「{token}」只在 params 中，未落到任何元素")
        elif token not in element_text:
            errors.append(f"内容段「{token}」未出现在元素中")
    return errors


def validate_placeholder_types(mg_def: dict[str, Any]) -> list[str]:
    """Catch params declared as color but used as text (03_compare accent bug)."""
    params = mg_def.get("params") or {}
    errors: list[str] = []
    for i, elem in enumerate(mg_def.get("elements") or []):
        if not isinstance(elem, dict) or elem.get("type") not in ("text", "caption"):
            continue
        content = str(elem.get("content") or "")
        for key, spec in params.items():
            if not isinstance(spec, dict) or str(spec.get("type", "")) != "color":
                continue
            if "{" + str(key) + "}" in content:
                errors.append(f"元素 #{i} 把 color 参数 {{{key}}} 当作文本使用")
    return errors


def validate_layout_overlaps(mg_def: dict[str, Any]) -> list[str]:
    """Cheap geometric guard for text/line overlap (top-left coordinate contract)."""
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    errors: list[str] = []
    texts = [
        (i, float(e.get("y") or 0), float(e.get("font_size") or 48))
        for i, e in enumerate(elements)
        if e.get("type") == "text" and isinstance(e.get("y"), (int, float))
    ]
    for j, e in enumerate(elements):
        if e.get("type") != "line" or not isinstance(e.get("y"), (int, float)):
            continue
        ly = float(e["y"])
        for i, ty, fs in texts:
            if ty < ly < ty + fs * 1.2:
                errors.append(f"line #{j} 穿过 text #{i} 的纵向范围")
                break
    return errors
