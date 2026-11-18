"""Deterministic animation QA helpers (text readability / motion density)."""
from __future__ import annotations

import re
from typing import Any

_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _resolved(elem: dict[str, Any], params: dict[str, Any]) -> str:
    text = str(elem.get("content") or "")
    def repl(m: re.Match[str]) -> str:
        spec = params.get(m.group(1))
        return str(spec.get("default", "")) if isinstance(spec, dict) else ""
    return _PARAM_RE.sub(repl, text) or text


def read_seconds(text: str) -> float:
    """Conservative reading time: English 15 chars/s, Chinese 4.5 chars/s."""
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    latin = max(0, len(text) - cjk)
    return round(cjk / 4.5 + latin / 15.0 + 1.5, 2)


def qa_text(mg_def: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    width = float(mg_def.get("width") or 1920)
    height = float(mg_def.get("height") or 1080)
    dur = float(mg_def.get("duration_sec") or 0)
    params = mg_def.get("params") if isinstance(mg_def.get("params"), dict) else {}
    for i, e in enumerate(mg_def.get("elements") or []):
        if not isinstance(e, dict) or e.get("type") != "text":
            continue
        text = _resolved(e, params).strip()
        fs = float(e.get("font_size") or 48)
        if fs < height * 0.04:
            issues.append(f"text #{i} 字号 {fs:.1f} < 画面高度 4%")
        need = read_seconds(text)
        if dur > 0 and need > dur:
            issues.append(f"text #{i} 阅读需 {need}s > 镜头 {dur}s")
        if len(text) > 16 and fs < 64:
            issues.append(f"text #{i} 超过 16 字且字号偏小")
        if len(text) > 24:
            issues.append(f"text #{i} 超过 24 字，应拆句")
    return issues


def qa_motion(mg_def: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
    moving = 0
    for i, e in enumerate(elements):
        kfs = e.get("keyframes") or []
        props = set()
        for kf in kfs:
            if isinstance(kf, dict):
                props.update(k for k in kf if k not in ("time", "easing"))
        if props:
            moving += 1
        elif e.get("type") in ("text", "shape"):
            issues.append(f"element #{i} 无关键帧，整镜静止")
    if len(elements) > 6:
        issues.append(f"同镜元素 {len(elements)} > 6")
    if moving == 0:
        issues.append("没有任何可观测运动")
    return issues


def qa_report(mg_def: dict[str, Any]) -> dict[str, Any]:
    text = qa_text(mg_def)
    motion = qa_motion(mg_def)
    return {"ok": not text and not motion, "text_issues": text, "motion_issues": motion}


def phone_report(mg_def: dict[str, Any], phone_w: int = 390, phone_h: int = 844) -> list[str]:
    """Approximate phone readability: scale the 16:9 frame into a phone viewport."""
    width = float(mg_def.get("width") or 1920)
    height = float(mg_def.get("height") or 1080)
    scale = min(phone_w / width, phone_h / height)
    issues: list[str] = []
    for i, e in enumerate(mg_def.get("elements") or []):
        if isinstance(e, dict) and e.get("type") == "text":
            fs = float(e.get("font_size") or 48) * scale
            if fs < phone_h * 0.04:
                issues.append(f"text #{i} 手机字号 {fs:.1f}px < 4% 屏高")
    return issues
