"""Deterministic MG layout normalization.

The model is good at intent, but bad at absolute coordinates.  This module
does not invent a new animation: it only clamps text boxes into a safe area,
unifies text groups and lays out simple horizontal flows (nodes + arrows).
"""
from __future__ import annotations

import copy
import re
from typing import Any

_SAFE_RATIO = 0.025
_ARROW_CHARS = set("→←↑↓➜➝➞")
_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolved_text(elem: dict[str, Any], params: dict[str, Any]) -> str:
    text = str(elem.get("content") or elem.get("text") or "")
    def repl(match: re.Match[str]) -> str:
        spec = params.get(match.group(1))
        return str(spec.get("default", "")) if isinstance(spec, dict) else ""
    return _PARAM_RE.sub(repl, text) or text


def estimate_text_width(text: str, font_size: float, letter_spacing: float = 0.0) -> float:
    """Conservative text width estimate for CJK + latin + emoji."""
    size = max(1.0, float(font_size))
    width = 0.0
    for ch in str(text):
        code = ord(ch)
        if code > 0x2E80:  # CJK / emoji / symbol
            width += size
        elif ch == " ":
            width += size * 0.30
        else:
            width += size * 0.60
    return width + max(0.0, float(letter_spacing)) * max(0, len(text) - 1)


def _text_box(elem: dict[str, Any], params: dict[str, Any], width: float, height: float) -> dict[str, float]:
    fs = _num(elem.get("font_size"), 48.0)
    spacing = _num(elem.get("letter_spacing"), 0.0)
    text = _resolved_text(elem, params)
    w = _num(elem.get("width"), 0.0) or estimate_text_width(text, fs, spacing)
    h = _num(elem.get("height"), 0.0) or fs * 1.2
    x = elem.get("x", "center")
    y = elem.get("y", "center")
    if isinstance(x, (int, float)):
        left = _num(x)
    elif x == "left":
        left = width * _SAFE_RATIO
    elif x == "right":
        left = width - width * _SAFE_RATIO - w
    else:
        left = (width - w) / 2
    if isinstance(y, (int, float)):
        top = _num(y)
    elif y == "top":
        top = height * _SAFE_RATIO
    elif y == "bottom":
        top = height - height * _SAFE_RATIO - h
    else:
        top = (height - h) / 2
    return {"left": left, "top": top, "w": w, "h": h}


def _set_xy(elem: dict[str, Any], left: float, top: float, width: float, height: float, box_w: float) -> None:
    # Keep center/edge anchors untouched; numeric boxes are top-left.
    if isinstance(elem.get("x"), (int, float)):
        elem["x"] = round(max(width * _SAFE_RATIO, min(left, width - width * _SAFE_RATIO - box_w)), 2)
    if isinstance(elem.get("y"), (int, float)):
        elem["y"] = round(max(height * _SAFE_RATIO, min(top, height - height * _SAFE_RATIO - _num(elem.get("font_size"), 48) * 1.2)), 2)


def _clamp_text(elem: dict[str, Any], params: dict[str, Any], width: float, height: float) -> None:
    fs = _num(elem.get("font_size"), 48.0)
    box = _text_box(elem, params, width, height)
    margin_x, margin_y = width * _SAFE_RATIO, height * _SAFE_RATIO
    max_w = width - 2 * margin_x
    if box["w"] > max_w:
        elem["font_size"] = round(max(12.0, fs * max_w / box["w"]), 2)
        box = _text_box(elem, params, width, height)
    if isinstance(elem.get("x"), (int, float)):
        elem["x"] = round(max(margin_x, min(_num(elem.get("x")), width - margin_x - box["w"])), 2)
    if isinstance(elem.get("y"), (int, float)):
        elem["y"] = round(max(margin_y, min(_num(elem.get("y")), height - margin_y - box["h"])), 2)


def _is_arrow(elem: dict[str, Any], params: dict[str, Any]) -> bool:
    text = _resolved_text(elem, params).strip()
    return bool(text) and all(ch in _ARROW_CHARS for ch in text)


def _center_text_in_cards(elements: list[dict[str, Any]], params: dict[str, Any], width: float, height: float) -> None:
    cards = [
        e for e in elements
        if e.get("type") == "shape" and _num(e.get("width"), 0) >= 120 and _num(e.get("height"), 0) >= 80
    ]
    for e in elements:
        if e.get("type") != "text" or not isinstance(e.get("x"), (int, float)) or not isinstance(e.get("y"), (int, float)):
            continue
        box = _text_box(e, params, width, height)
        cx, cy = box["left"] + box["w"] / 2, box["top"] + box["h"] / 2
        # 先按纵向重叠找候选卡片，再选横向最近的一张。
        # 旧实现要求文字中心已在卡片内；模型把文字放到卡片外时永远不会命中。
        candidates = []
        for card in cards:
            left = _num(card.get("x"), 0)
            top = _num(card.get("y"), 0)
            cw, ch = _num(card.get("width"), 0), _num(card.get("height"), 0)
            if top <= cy <= top + ch and left - 120 <= cx <= left + cw + 120:
                candidates.append((abs(cx - (left + cw / 2)), left, top, cw, ch))
        if candidates:
            _, left, top, cw, ch = min(candidates)
            e["x"] = round(left + (cw - box["w"]) / 2, 2)
            e["y"] = round(top + (ch - box["h"]) / 2, 2)


def _dedupe_quote_text(elements: list[dict[str, Any]], params: dict[str, Any]) -> None:
    texts = [e for e in elements if e.get("type") == "text"]
    drop: set[int] = set()
    for i, a in enumerate(texts):
        at = _resolved_text(a, params).strip()
        if not at:
            continue
        for j, b in enumerate(texts):
            bt = _resolved_text(b, params).strip()
            if i != j and len(at) < len(bt) and at in bt and len(at) <= len(bt) * 0.7:
                drop.add(i)
                break
    if drop:
        for idx in sorted(drop, reverse=True):
            elements.remove(texts[idx])


def _unify_text_group(group: list[dict[str, Any]], params: dict[str, Any]) -> None:
    sizes = [_num(e.get("font_size"), 48.0) for e in group]
    size = sorted(sizes)[len(sizes) // 2] if sizes else 48.0
    bg = next((str(e.get("background")) for e in group if e.get("background")), "")
    color = next((str(e.get("font_color")) for e in group if e.get("font_color")), "")
    for e in group:
        e["font_size"] = round(size, 2)
        if bg:
            e["background"] = bg
        if color:
            e["font_color"] = color


def normalize_mg_layout(mg_def: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a normalized copy + human-readable adjustments."""
    out = copy.deepcopy(mg_def)
    width = _num(out.get("width"), 1920) or 1920
    height = _num(out.get("height"), 1080) or 1080
    params = out.get("params") if isinstance(out.get("params"), dict) else {}
    elements = [e for e in (out.get("elements") or []) if isinstance(e, dict)]
    warnings: list[str] = []

    for e in elements:
        if e.get("type") == "text":
            _clamp_text(e, params, width, height)

    texts = [e for e in elements if e.get("type") == "text"]
    shot = str(out.get("shot_type") or "").lower()
    # 只有明确的流程镜头才做节点重排；comparison 等镜头有独立卡片，
    # 不能被“3 个文本”误判成 flow（03 的标签被推到卡片外就是这个原因）。
    flow = shot in ("flow", "process")
    _center_text_in_cards(elements, params, width, height)
    if flow:
        nodes = [e for e in texts if not _is_arrow(e, params)]
        arrows = [e for e in texts if _is_arrow(e, params)]
        nodes.sort(key=lambda e: _num(e.get("x"), 0))
        if len(nodes) >= 2:
            _unify_text_group(nodes, params)
            boxes = [_text_box(e, params, width, height) for e in nodes]
            safe_l, safe_r = width * _SAFE_RATIO, width - width * _SAFE_RATIO
            # 用 left 坐标分布：首节点贴安全左线，尾节点贴安全右线，
            # 中间节点按可用宽度等距；节点的 x 是左上角，不是中心。
            usable_left = safe_l
            usable_right = safe_r - boxes[-1]["w"]
            gap = (usable_right - usable_left) / max(1, len(nodes) - 1)
            lefts = [usable_left + i * gap for i in range(len(nodes))]
            center_y = sum(b["top"] + b["h"] / 2 for b in boxes) / len(boxes)
            for e, box, left in zip(nodes, boxes, lefts):
                e["x"] = round(left, 2)
                e["y"] = round(center_y - box["h"] / 2, 2)
                warnings.append(f"flow 节点 {str(e.get('content'))[:12]} 自动等距")
            centers = [left + box["w"] / 2 for left, box in zip(lefts, boxes)]
            for i, arrow in enumerate(arrows[:len(nodes) - 1]):
                left = centers[i] + boxes[i]["w"] / 2
                right = centers[i + 1] - boxes[i + 1]["w"] / 2
                arrow["x"] = round((left + right) / 2 - _num(arrow.get("font_size"), 48) / 2, 2)
                arrow["y"] = round(center_y - _num(arrow.get("font_size"), 48) / 2, 2)
                warnings.append(f"flow 箭头 #{i + 1} 自动居中")
    elif len(texts) >= 2 and all(str(e.get("x", "")) == "center" for e in texts):
        # Quote / multi-line title: remove repeated sublines, then unify.
        _dedupe_quote_text(elements, params)
        texts = [e for e in elements if e.get("type") == "text"]
        # Quote / multi-line title: same size + vertical stack, centered.
        sizes = [_num(e.get("font_size"), 48) for e in texts]
        fs = sorted(sizes)[len(sizes) // 2]
        boxes = [_text_box(e, params, width, height) for e in texts]
        order = sorted(range(len(texts)), key=lambda i: boxes[i]["top"])
        h = fs * 1.2
        gap = fs * 0.35
        total = len(texts) * h + (len(texts) - 1) * gap
        center_y = sum(b["top"] + b["h"] / 2 for b in boxes) / len(boxes)
        top = center_y - total / 2
        _unify_text_group(texts, params)
        for rank, idx in enumerate(order):
            texts[idx]["y"] = round(max(height * _SAFE_RATIO, top + rank * (h + gap)), 2)
            warnings.append("多行文字统一字号/居中/等距")

    return out, warnings
