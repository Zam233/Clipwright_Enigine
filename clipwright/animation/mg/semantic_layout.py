"""LLM-facing layout checks: errors are fed back, not silently normalized."""
from __future__ import annotations

import re
from typing import Any

_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _text(e: dict, params: dict[str, Any] | None = None) -> str:
    raw = str(e.get("content") or "").strip()
    params = params or {}
    def repl(m: re.Match[str]) -> str:
        spec = params.get(m.group(1))
        return str(spec.get("default", "")) if isinstance(spec, dict) else ""
    return _PARAM_RE.sub(repl, raw).strip() or raw




def _hex_lum(v: str) -> float | None:
    v = (v or "").strip()
    if not v.startswith("#") or len(v) not in (4, 7):
        return None
    if len(v) == 4:
        v = "#" + "".join(c * 2 for c in v[1:])
    try:
        r, g, b = (int(v[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

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
                errs.append(f"卡片标签 '{_text(t, params)}' 宽度≈卡片但未 text_align=center，会贴左边缘")
            elif abs(tx - cx) < 8 and tw < cw - 8 and str(t.get("text_align") or "left") == "left":
                errs.append(f"卡片标签 '{_text(t, params)}' 贴在卡片左边缘，应 x=card.x+(card.width-text.width)/2")

    # 2) flow arrows: line used as arrow without a head
    if str(mg_def.get("shot_type") or "") in ("flow", "process"):
        has_arrow_text = any("→" in _text(t, params) or "←" in _text(t, params) for t in texts)
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
                errs.append(f"多行文本 '{_text(a, params)}' 与 '{_text(b, params)}' 共用 y/bbox；必须同 x、不同 y，y 递增")
                break
    # 4) duplicate phrases
    contents = [_text(t, params) for t in texts if _text(t, params)]
    for i, a in enumerate(contents):
        for b in contents[i + 1:]:
            if a and b and (a == b or a in b or b in a) and min(len(a), len(b)) >= 2:
                errs.append(f"重复文本：'{a}' 与 '{b}' 表达同一内容，只保留一个或拆行")
    # 5) units must be attached to number labels, not separate elements
    for t in texts:
        if _text(t, params).strip() in ("%", "px", "s", "秒"):
            errs.append(f"单位 '{_text(t, params)}' 被单独做成元素；必须并入相邻数值标签")
    # 6) flow safe area
    if str(mg_def.get("shot_type") or "") in ("flow", "process"):
        for t in texts:
            tx = _num(t.get("x")); tw = _num(t.get("width"))
            if tx < 200 or (tw > 0 and tx + tw > 1720):
                errs.append(f"流程节点 '{_text(t, params)}' 超出 240px 安全区；x 应在 240..1680-width")
    # 7) transparent canvas + near-black text = invisible in rendered black background
    bg = str((mg_def.get("style") or {}).get("background") or "transparent").lower()
    if bg in ("transparent", "none", ""):
        for t in texts:
            col = t.get("font_color") or t.get("color") or ""
            lum = _hex_lum(str(col))
            if lum is not None and lum < 0.2:
                errs.append(f"文字 '{_text(t, params)}' 在透明画布上使用近黑色 {col}，渲染后不可见；改浅色 (#E8F0F7/#FFFFFF)")
    return errs


def semantic_layout_failures(mg_def: dict[str, Any], params: dict[str, Any] | None = None) -> list[Any]:
    """Structured failures with element indices and numeric expectations."""
    from clipwright.animation.agentic.contracts import Failure

    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    out: list[Failure] = []
    cards = [(i, e) for i, e in enumerate(elements) if e.get("type") == "shape" and _num(e.get("width")) >= 200 and _num(e.get("height")) >= 120]
    texts = [(i, e) for i, e in enumerate(elements) if e.get("type") == "text"]
    for ci, card in cards:
        cx, cy = _num(card.get("x")), _num(card.get("y"))
        cw, ch = _num(card.get("width")), _num(card.get("height"))
        for ti, t in texts:
            tx, ty = _num(t.get("x")), _num(t.get("y"))
            tw = _num(t.get("width"))
            th = _num(t.get("height")) or _num(t.get("font_size"), 48) * 1.4
            if tx < cx - 4 or tx + max(tw, 1) > cx + cw + 4 or not (cy - 4 <= ty <= cy + ch + 4):
                continue
            align = str(t.get("text_align") or "left")
            if tw >= cw - 8 and align != "center":
                out.append(Failure("MG_LAYOUT_CARD_LABEL_OFFCENTER", "layout", scope="element",
                    location={"element_index": ti, "path": "elements[%d].text_align" % ti},
                    expected="center", observed=align, fix_hint="卡片标签设 text_align=center"))
            elif abs(tx - cx) < 8 and tw < cw - 8 and align == "left":
                out.append(Failure("MG_LAYOUT_CARD_LABEL_LEFT", "layout", scope="element",
                    location={"element_index": ti, "path": "elements[%d].x" % ti, "card_index": ci},
                    expected=cx + (cw - tw) / 2, observed=tx,
                    fix_hint="x = card.x + (card.width - text.width)/2"))
    if str(mg_def.get("shot_type") or "") in ("flow", "process"):
        heads = any(e.get("type") in ("shape", "circle") and _num(e.get("width"), 0) <= 40 and _num(e.get("height"), 0) <= 40 for e in elements)
        arrows = any("→" in _text(t, params) or "←" in _text(t, params) for _, t in texts)
        lines = [(i, e) for i, e in enumerate(elements) if e.get("type") == "line"]
        if lines and not (heads or arrows):
            out.append(Failure("MG_LAYOUT_ARROW_HEAD_MISSING", "layout", scope="document",
                location={"element_index": lines[0][0]}, expected="箭头头/→ 文本", observed="line 无头",
                fix_hint="改用 → 文本或加小三角 shape"))
    for ai in range(len(texts)):
        a_idx, a = texts[ai]
        for bi in range(ai + 1, len(texts)):
            b_idx, b = texts[bi]
            ay, by = _num(a.get("y")), _num(b.get("y"))
            ah = _num(a.get("height")) or _num(a.get("font_size"), 48) * 1.4
            bh = _num(b.get("height")) or _num(b.get("font_size"), 48) * 1.4
            if abs(_num(a.get("x")) - _num(b.get("x"))) < 8 and abs(_num(a.get("width")) - _num(b.get("width"))) < 8 and ay < by + bh and by < ay + ah:
                gap = max(ah, bh)
                out.append(Failure("MG_LAYOUT_MULTILINE_OVERLAP", "layout", scope="element",
                    location={"element_indices": [a_idx, b_idx], "path": "elements[*].y"},
                    expected={"min_gap": gap, "suggested_y": [ay, ay + gap]},
                    observed={"y": [ay, by], "heights": [ah, bh]},
                    fix_hint="同 x 且 y 递增；相邻 y 差 >= 最大行高"))
                break
    for ti, t in texts:
        if _text(t, params).strip() in ("%", "px", "s", "秒"):
            out.append(Failure("MG_LAYOUT_UNIT_SEPARATE", "layout", scope="element",
                location={"element_index": ti, "path": "elements[%d].content" % ti},
                expected="并入相邻数值标签", observed=_text(t, params),
                fix_hint="删除该元素，把单位追加到数值文本"))
    if str(mg_def.get("shot_type") or "") in ("flow", "process"):
        for ti, t in texts:
            tx, tw = _num(t.get("x")), _num(t.get("width"))
            if tx < 200 or (tw > 0 and tx + tw > 1720):
                out.append(Failure("MG_LAYOUT_FLOW_SAFE_AREA", "layout", scope="element",
                    location={"element_index": ti, "path": "elements[%d].x" % ti},
                    expected={"min_x": 240, "max_right": 1680}, observed={"x": tx, "width": tw},
                    fix_hint="节点置于 240..1680 安全区，等距排布"))
    bg = str((mg_def.get("style") or {}).get("background") or "transparent").lower()
    if bg in ("transparent", "none", ""):
        for ti, t in texts:
            col = t.get("font_color") or t.get("color") or ""
            lum = _hex_lum(str(col))
            if lum is not None and lum < 0.2:
                out.append(Failure("MG_TEXT_LOW_CONTRAST", "typography", scope="element",
                    location={"element_index": ti, "path": "elements[%d].font_color" % ti},
                    expected="#E8F0F7 或 #FFFFFF", observed=str(col),
                    fix_hint="透明画布上的文字改为浅色，否则渲染后不可见"))
    seen: list[tuple[str, str]] = []
    for idx, t in texts:
        txt = _text(t, params)
        if not txt:
            continue
        for other in seen:
            if txt == other[0] or txt in other[0] or other[0] in txt:
                out.append(Failure("MG_LAYOUT_DUPLICATE_TEXT", "layout", scope="element",
                    location={"element_index": idx}, expected="唯一条目", observed=txt,
                    fix_hint="删除重复文本或拆成语义不同行"))
                break
        seen.append((txt, ""))
    return out
