"""文字动画的 LLM 生成器（PRD `docs/text-animation-llm-prd-2026-10-30.md` M1/M2）。

与 `animation/mg/type_generation.py` 的关系：文字动画复用 **MGDef 契约**（elements /
keyframes / params / style），因此校验（`validate_mg_json`）、修复（`repair_mg_json`）、
渲染（`mg_fastpath` / drawtext 参数化）都能直接复用；不同的只是提示词族与
「避让字幕带 / 只做强调不做字幕」这两条硬约束。

对外只有 `generate_for_type()`：
- 成功 → MGDef（带 `text_meta`：type_id / anchor / avoid）
- 失败 → `ok=False`（调用方回退 `AnimationCatalog` 模板并登记降级）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from clipwright.animation.mg.validator import repair_mg_json, validate_mg_json
from clipwright.animation.text_style import (
    ensure_text_animation, normalize_style, validate_motion, validate_style,
)
from clipwright.animation.text_specs import (
    TextAnimSpec, get_text_spec, load_text_prompt, text_prompt_version,
)
from clipwright.config import logger

#: 字幕带高度（像素）：文字动画不得进入这条带（字幕固定在底部）
CAPTION_BAND_PX = 160
#: 单行字数上限（文字动画只做短语；长句交给字幕）
MAX_LINE_CHARS = 14
#: 元素数上限（装饰也算）
MAX_ELEMENTS = 3


@dataclass
class TextGenerationResult:
    ok: bool
    mg_def: dict[str, Any] = field(default_factory=dict)
    method: str = ""
    type_id: str = ""
    prompt_version: str = ""
    fixes: list[str] = field(default_factory=list)
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "type_id": self.type_id, "method": self.method,
            "prompt_version": self.prompt_version, "fixes": self.fixes,
            "error": self.error,
        }


def _extract_json(content: str) -> dict[str, Any] | None:
    """容错解析：裸 JSON → ```json 围栏 → 首 `{` 到末 `}`。"""
    if not content:
        return None
    try:
        parsed = json.loads(content.strip())
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if fence:
        try:
            parsed = json.loads(fence.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    start, end = content.find("{"), content.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(content[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def build_text_prompt(
    spec: TextAnimSpec,
    *,
    text: str,
    duration_s: float,
    persona: dict[str, Any] | None = None,
    caption_band: bool = True,
    category_hint: str = "",
) -> tuple[str, str]:
    """组装 (system, user) 提示词：类型六段 + persona + 避让约束 + 输出契约。"""
    sections = load_text_prompt(spec.id)
    persona = persona or {}
    palette_bits = [
        f"- {label}: {persona[key]}"
        for key, label in (
            ("primary_color", "主色"), ("accent_color", "强调色"),
            ("text_color", "文字色"), ("font", "字体"),
            ("palette", "配色描述"), ("style_description", "风格描述"),
        )
        if persona.get(key)
    ]

    system_parts = [
        "你是动态排版（kinetic typography）设计师，负责把旁白里的**关键词/短语**"
        "做成屏幕上的文字动效。你只输出 MGDef JSON，不解释、不加围栏。",
        f"## 类型\n{spec.name}（{spec.id}）：{spec.intent}",
    ]
    for name in ("Semantic constraints", "Composition", "Motion", "Anti-patterns", "Example"):
        body = sections.get(name, "")
        if body:
            system_parts.append(f"## {name}\n{body}")
    if sections.get("Style reference"):
        system_parts.append(f"## Style reference\n{sections['Style reference']}")

    user_parts = [
        f"## 本段旁白\n{text.strip()[:600]}",
        f"## 时长\n片段 {max(0.5, float(duration_s)):.2f} 秒；"
        "mg_def.duration_sec 必须等于该值，关键帧时间落在 [0, 时长] 内。",
    ]
    if palette_bits:
        user_parts.append("## 创作者视觉风格（配色/字体必须一致）\n" + "\n".join(palette_bits))
    if category_hint:
        user_parts.append(f"## 视频类型语境\n{category_hint}")
    if caption_band:
        # 注意：这里必须用 f-string（不能用 % 格式化）——正文里的「70%」会被当成格式符。
        user_parts.append(
            "## 版面避让（强制）\n"
            f"画面底部 {CAPTION_BAND_PX}px 是字幕带，已由字幕轨占用：\n"
            "- 所有元素必须落在画布上部 70%（y 不超过画布高度的 70%）；\n"
            "- 默认 anchor=center；不允许 position=bottom；\n"
            f"- 文本必须是旁白的摘要（≤{MAX_LINE_CHARS} 字/行），**禁止与字幕逐字相同**。"
        )
    user_parts.append(
        "## 输出硬性要求\n"
        f"- 元素数 1–{MAX_ELEMENTS}（含装饰）；每条文本 ≤{MAX_LINE_CHARS} 字。\n"
        "- 至少两个非装饰元素有可观测变化（opacity/scale/translate_x/translate_y），"
        "关键帧覆盖 ≥60% 时长，入场 <1.2s，末段 ≥0.5s 静止。\n"
        "- **文字必须带阴影**：每个 text 元素显式给 `text_shadow`（推荐 `0 4px 10px rgba(0,0,0,0.78)`）；"
        "浅色/蓝色底板/高亮条上加强到 `0 5px 14px rgba(0,0,0,0.85)`。没有阴影视为不合格。\n"
        "- `text_animation.style` 必须同时给出 shadow_x / shadow_y / shadow_blur / shadow_color，不能留空。\n"
        "- 禁止不透明全幅背景。\n"
        "- 必须额外输出顶层 `text_animation`：\n"
        "  {\"style\": {font/font_size/font_weight/letter_spacing/text_align/fill/box/box_color/box_radius/box_padding_x/box_padding_y/stroke_width/stroke_color/shadow_x/shadow_y/shadow_blur/shadow_color/glow_color/glow_width/position/offset_x/offset_y}, "
        "\"motion\": {type, reveal_sec, cursor, stagger_sec, easing}, "
        "\"display_sec\": 本关键词应停留多少秒（按内容阅读需求，不设硬上限）, "
        "\"duration_reason\": 为什么需要这么久（阅读/多行/引用/停顿）}\n"
        "- 颜色/字号/描边/阴影/底板必须显式给出，禁止默认白字无描边；同一支片必须有样式变化：至少用强调色/底板/不同字号区分层级，禁止所有关键词都是白字无底板。motion.type 只能是 "
        "fade_in / fade_out / slide_in / scale_in / typewriter / word_by_word / highlight / glitch / neon。\n"
        '- 只输出 MGDef JSON，且必须包含字段：animation_id / duration_sec / width / height / elements / text_animation。'
    )
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


#: 模型可能用的"文字内容"字段名（真实 LLM 反复换着用）
_CONTENT_KEYS = ("content", "text", "label", "title", "value", "caption", "words")
#: 模型可能用的"关键帧时间"字段名
_TIME_KEYS = ("time", "t", "at", "time_sec", "offset", "ts")
#: 关键帧数组可能挂在这些键下
_KF_KEYS = ("keyframes", "frames", "keyframe", "keys")


def _first_text(elem: dict[str, Any]) -> str | None:
    for key in _CONTENT_KEYS:
        value = elem.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _normalize_keyframes(elem: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    """关键帧归一化：时间键名兜底 + 缺失时间**均匀铺开**。

    为什么必须铺开（E2E 2026-10-30 实测）：模型有时只给 `{opacity: 0}` / `{scale: 1.2}`
    不给 `time` → 全部落到 0.0 → 动效覆盖度 0%、后段完全静止，最终被记成
    `text_motion_weak` 并回退模板。缺时间时按顺序铺到 [0, duration] 才是它想要的效果。
    """
    raw: list[Any] = []
    for key in _KF_KEYS:
        value = elem.get(key)
        if isinstance(value, list) and value:
            raw = value
            break
    frames = [kf for kf in raw if isinstance(kf, dict)]
    if not frames:
        return []
    out: list[dict[str, Any]] = []
    missing: list[int] = []
    for idx, kf in enumerate(frames):
        t: float | None = None
        for key in _TIME_KEYS:
            if key in kf:
                try:
                    t = float(kf[key])
                except (TypeError, ValueError):
                    t = None
                break
        copy = dict(kf)
        if t is None:
            missing.append(idx)
        else:
            copy["time"] = t
        out.append(copy)
    if missing:
        total = max(1, len(frames) - 1)
        for idx in missing:
            out[idx]["time"] = round(duration * idx / total, 3)
    for kf in out:
        try:
            t = float(kf.get("time") or 0)
        except (TypeError, ValueError):
            t = 0.0
        kf["time"] = round(min(max(0.0, t), duration), 3)
    out.sort(key=lambda item: float(item.get("time") or 0))
    return out


def _normalize(mg_def: dict[str, Any], spec: TextAnimSpec, duration_s: float) -> dict[str, Any]:
    """补齐/夹取字段：duration、元素上限、文本截断、锚点与避让标记。

    2026-10-30 真实 LLM 实测：模型会给 `type: "number"` / `"counter"` 这类
    不在 MGDef 白名单里的元素类型，还会把关键帧时间写到片段时长之外
    （`time must be between 0 and 3.0`）——不归一化就会被校验直接判死，
    然后整段退化成模板。文字动画的元素本质都是文字，统一收敛到 `text`，
    并把关键帧时间夹进 [0, duration]。
    """
    duration = max(0.5, float(duration_s))
    mg_def["duration_sec"] = round(duration, 3)
    valid_types = {"text", "shape", "line", "circle", "ring", "arc", "bg", "image"}
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    trimmed: list[dict[str, Any]] = []
    for elem in elements[:MAX_ELEMENTS]:
        elem_type = str(elem.get("type") or "text").strip().lower()
        if elem_type not in valid_types:
            elem["type"] = "text"
        # 模型常把文字放在 `text`/`label`/`title`/`value` 而不是 `content`
        # → 校验会判 "text element missing 'content'"（真实 LLM 实测）。
        # 统一收敛到 content；实在没有文字的 text 元素**直接丢掉**（保留纯装饰元素
        # 不报错），而不是让整段退化成模板。
        content = _first_text(elem)
        if content is None and elem["type"] == "text":
            continue
        if content is not None:
            text = content if len(content) <= MAX_LINE_CHARS else content[:MAX_LINE_CHARS]
            elem["content"] = text
        keyframes = _normalize_keyframes(elem, duration)
        if not keyframes:
            continue
        elem["keyframes"] = keyframes
        trimmed.append(elem)
    mg_def["elements"] = trimmed
    meta = dict(mg_def.get("text_meta") or {})
    meta.update({
        "type_id": spec.id,
        "anchor": spec.default_anchor,
        "avoid_caption_band_px": CAPTION_BAND_PX,
        "max_line_chars": MAX_LINE_CHARS,
    })
    mg_def["text_meta"] = meta
    # 文字动画样式/动效/时长契约（PRD M1）：缺失时从主元素推断 legacy
    try:
        ensure_text_animation(
            mg_def,
            text=str(mg_def.get("text") or mg_def.get("content") or ""),
            host_duration=duration,
        )
    except Exception as exc:  # noqa: BLE001 — 样式归一失败不阻断产物
        logger.warning("text_generation: text_animation 归一失败: %s", exc)
    return mg_def


def _text_easing_backup(mg_def: dict[str, Any]) -> list[tuple[int, int, Any]]:
    backup: list[tuple[int, int, Any]] = []
    for i, elem in enumerate(mg_def.get("elements") or []):
        if not isinstance(elem, dict):
            continue
        for j, kf in enumerate(elem.get("keyframes") or []):
            if isinstance(kf, dict) and "easing" in kf:
                backup.append((i, j, kf.get("easing")))
    return backup


def _restore_text_easing(mg_def: dict[str, Any], backup: list[tuple[int, int, Any]]) -> None:
    for i, j, value in backup:
        elems = mg_def.get("elements") or []
        if i >= len(elems) or not isinstance(elems[i], dict):
            continue
        kfs = elems[i].get("keyframes") or []
        if j < len(kfs) and isinstance(kfs[j], dict):
            kfs[j]["easing"] = value


def _validate_text_easing_neutral(mg_def: dict[str, Any], *, duration_s: float) -> list[str]:
    """文字动画校验：把 drawtext 的 easing 先换成 MG 白名单里的占位值。

    根因（2026-11-15 E2E）：`prompts/text/_base.md` 要求模型写 drawtext 的
    `ease-out-cubic` 等 21 个名字（并禁止 back-out/ease），但
    `validate_text_def` 直接复用 MG validator（白名单恰好相反）——按提示词
    输出的 JSON 必被判非法，重试两次后回退模板。文字动效由 drawtext 渲染，
    easing 合法性以 `keyframes_expr.EASING_EXPR` 为准，不应被 MG 词表否决。
    """
    import copy

    from clipwright.utils.keyframes_expr import EASING_EXPR

    probe = copy.deepcopy(mg_def)
    for elem in probe.get("elements") or []:
        if not isinstance(elem, dict):
            continue
        for kf in elem.get("keyframes") or []:
            if isinstance(kf, dict) and isinstance(kf.get("easing"), str):
                if kf["easing"].strip() in EASING_EXPR:
                    kf["easing"] = "ease"
    return validate_text_def(probe, duration_s=duration_s)


def validate_text_def(
    mg_def: dict[str, Any], *, duration_s: float,
) -> list[str]:
    """在 MGDef 校验之上追加文字动画专属约束（越界字幕带/文本过长/元素过多）。"""
    errors: list[str] = []
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    if not elements:
        errors.append("elements 为空")
    if len(elements) > MAX_ELEMENTS:
        errors.append(f"元素数 {len(elements)} 超过上限 {MAX_ELEMENTS}")
    for idx, elem in enumerate(elements):
        content = str(elem.get("content") or elem.get("text") or "")
        if len(content) > MAX_LINE_CHARS:
            errors.append(f"elements[{idx}] 文本 {len(content)} 字超过 {MAX_LINE_CHARS}")
        position = str(elem.get("position") or "")
        if position == "bottom":
            errors.append(f"elements[{idx}] position=bottom 会压字幕带")
        try:
            y = float(elem.get("y") or 0)
        except (TypeError, ValueError):
            y = 0.0
        # y 为像素时检查是否落入字幕带；为 0（未给）时忽略
        height = float(mg_def.get("height") or 1080)
        if y > 0 and y > height - CAPTION_BAND_PX:
            errors.append(f"elements[{idx}] y={y:.0f} 落入底部字幕带")
    ta = mg_def.get("text_animation") or {}
    if not isinstance(ta, dict) or not ta:
        errors.append("缺少 text_animation（样式/动效/时长契约）")
    else:
        errors.extend(validate_style(normalize_style(ta.get("style") or {}))[:4])
        errors.extend(validate_motion(ta.get("motion") or {}, text=" ".join(
            str(e.get("content") or "") for e in elements
        ))[:4])
        if not str(ta.get("duration_reason") or "").strip():
            errors.append("text_animation.duration_reason 为空（时长必须说明内容依据）")
        if float(ta.get("display_sec") or 0) <= 0:
            errors.append("text_animation.display_sec 必须 > 0")
    ok, base_errors = validate_mg_json(mg_def)
    if not ok:
        errors.extend(base_errors[:8])
    return errors


async def generate_for_type(
    type_id: str,
    *,
    text: str,
    duration_s: float,
    persona: dict[str, Any] | None = None,
    caption_band: bool = True,
    category_hint: str = "",
    llm: Any = None,
    width: int = 1920,
    height: int = 1080,
    max_attempts: int = 2,
) -> TextGenerationResult:
    """按类型提示词让 LLM 生成文字动画 MGDef（失败返回 ok=False，由调用方回退模板）。"""
    spec = get_text_spec(type_id)
    if spec is None:
        return TextGenerationResult(ok=False, type_id=type_id,
                                    error=f"未注册的文字动画类型: {type_id}")
    system_prompt, user_prompt = build_text_prompt(
        spec, text=text, duration_s=duration_s, persona=persona,
        caption_band=caption_band, category_hint=category_hint,
    )
    version = text_prompt_version(spec.id)

    if llm is None:
        from clipwright.services.llm import LLMService
        llm = LLMService()

    last_error = ""
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            from clipwright.services.llm import MAX_OUTPUT_TOKENS

            resp = await llm.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=MAX_OUTPUT_TOKENS,
                timeout=300,
            )
            content = getattr(resp, "content", "") or ""
            parsed = _extract_json(content)
            if not parsed:
                last_error = f"第 {attempt} 次返回不可解析（chars={len(content)}）"
                logger.warning("text_generation: %s type=%s", last_error, type_id)
                continue
            mg_def = _normalize(parsed, spec, duration_s)
            mg_def.setdefault("width", width)
            mg_def.setdefault("height", height)
            mg_def.setdefault("animation_id", f"text_{spec.id}")
            errors = _validate_text_easing_neutral(mg_def, duration_s=duration_s)
            if errors:
                # 修复会归一/删除 easing；文字动画必须保留 drawtext 的 easing 名，
                # 所以修复后按索引还原，再按“easing 中性副本”复验。
                _easing = _text_easing_backup(mg_def)
                repaired, fixes = repair_mg_json(dict(mg_def))
                _restore_text_easing(repaired, _easing)
                repaired = _normalize(repaired, spec, duration_s)
                _restore_text_easing(repaired, _easing)
                again = _validate_text_easing_neutral(repaired, duration_s=duration_s)
                if again:
                    last_error = "校验失败：" + "；".join(again[:3])
                    logger.warning("text_generation: %s type=%s", last_error, type_id)
                    continue
                return TextGenerationResult(
                    ok=True, mg_def=repaired, method="text_repair",
                    type_id=spec.id, prompt_version=version, fixes=fixes,
                )
            return TextGenerationResult(
                ok=True, mg_def=mg_def, method="text_llm",
                type_id=spec.id, prompt_version=version,
            )
        except Exception as exc:  # noqa: BLE001 — LLM 失败由调用方回退模板
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("text_generation 调用失败 type=%s: %s", type_id, last_error)

    return TextGenerationResult(ok=False, type_id=spec.id, prompt_version=version,
                                method="text_failed", error=last_error[:300])


__all__ = [
    "CAPTION_BAND_PX", "MAX_ELEMENTS", "MAX_LINE_CHARS", "TextGenerationResult",
    "build_text_prompt", "generate_for_type", "validate_text_def",
]
