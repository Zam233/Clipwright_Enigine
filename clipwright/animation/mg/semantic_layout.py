"""LLM-facing layout checks: errors are fed back, not silently normalized."""
from __future__ import annotations

from typing import Any


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _text(e: dict) -> str:
    return str(e.get("content") or "").strip()


def semantic_layout_errors(mg_def: dict[str, Any], params: dict[str, Any] | None = None) -> list[str]:
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    errs: list[str] = []
    cards = [e for e in elements if e.get("type") == "shape" and _num(e.get("width")) >= 200 and _num(e.get("height")) >= 120]
    texts = [e for e in elements if e.get("type") == "text"]

    # 1) card label: same width as card but not centered, or stuck to left edge
    for card in cards:
        cx, cy = _num(card.get("x")), _num(card.get("y"))
        cw, ch = _num(card.get("width")), _num(card.get("height"))
        for t in texts:
            tx, ty = _num(t.get("x")), _num(t.get("y"))
            tw, th = _num(t.get("width")), _num(t.get("height")) or _num(t.get("font_size"), 48) * 1.4
            if tx < cx - 4 or tx + max(tw, 1) > cx + cw + 4:
                continue
            if not (cy - 4 <= ty <= cy + ch + 4):
                continue
            if tw >= cw - 8 and str(t.get("text_align") or "left") != "center":
                errs.append(f"卡片标签 '{_text(t)}' 宽度≈卡片但未 text_align=center，会贴左边缘")
            elif abs(tx - cx) < 8 and tw < cw - 8 and str(t.get("text_align") or "left") == "left":
                errs.append(f"卡片标签 '{_text(t)}' 贴在卡片左边缘，应 x=card.x+(card.width-text.width)/2")

    # 2) flow arrows: line used as arrow without a head
    if str(mg_def.get("shot_type") or "") in ("flow", "process"):
        has_arrow_text = any("→" in _text(t) or "←" in _text(t) for t in texts)
        has_line = any(e.get("type") == "line" for e in elements)
        has_head = any(e.get("type") in ("shape", "circle") and _num(e.get("width"), 0) <= 40 and _num(e.get("height"), 0) <= 40 for e in elements)
        if has_line and not (has_arrow_text or has_head):
            errs.append("流程用了 line 但当箭头：必须改用文本 →，或额外加箭头头 shape；禁止无头 line")

    # 3) multi-line duplicate/overlap
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            ay, by = _num(a.get("y")), _num(b.get("y"))
            ah = _num(a.get("height")) or _num(a.get("font_size"), 48) * 1.4
            bh = _num(b.get("height")) or _num(b.get("font_size"), 48) * 1.4
            if abs(_num(a.get("x")) - _num(b.get("x"))) < 8 and abs(_num(a.get("width")) - _num(b.get("width"))) < 8 and ay < by + bh and by < ay + ah:
                errs.append(f"多行文本 '{_text(a)}' 与 '{_text(b)}' 共用 y/bbox；必须同 x、不同 y，y 递增")
                break
    # 4) duplicate phrases
    contents = [_text(t) for t in texts if _text(t)]
    for i, a in enumerate(contents):
        for b in contents[i + 1:]:
            if a and b and (a == b or a in b or b in a) and min(len(a), len(b)) >= 2:
                errs.append(f"重复文本：'{a}' 与 '{b}' 表达同一内容，只保留一个或拆行")
    return errs
