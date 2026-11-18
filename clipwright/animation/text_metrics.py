"""Real browser text bbox measurement for MG text elements."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from clipwright.animation.mg.layout import estimate_text_width


def measure_text_bboxes(mg_def: dict[str, Any]) -> list[dict[str, Any]]:
    """Render mg_def in Chromium and return real text boxes.

    Falls back to deterministic estimates when Playwright/browser is unavailable.
    """
    width = int(mg_def.get("width") or 1920)
    height = int(mg_def.get("height") or 1080)
    try:
        from clipwright.animation.mg_renderer import MGRenderer
        from playwright.sync_api import sync_playwright

        params = {
            str(k): str(v.get("default", "")) if isinstance(v, dict) else str(v)
            for k, v in (mg_def.get("params") or {}).items()
        }
        html = MGRenderer.render(mg_def, params, width=width, height=height, fps=30)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mg.html"
            p.write_text(html, encoding="utf-8")
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(p.as_uri())
                page.wait_for_timeout(200)
                boxes = page.evaluate("""() => Array.from(document.querySelectorAll('.mg-el')).map((el) => {
                    const r = el.getBoundingClientRect();
                    const cs = getComputedStyle(el);
                    return {tag: el.tagName, text: (el.textContent || '').slice(0, 80),
                            x: r.x, y: r.y, w: r.width, h: r.height,
                            font: cs.fontFamily, size: parseFloat(cs.fontSize) || 0};
                }).filter((x) => x.size > 0)""")
                browser.close()
                return boxes
    except Exception:
        params = mg_def.get("params") or {}
        out: list[dict[str, Any]] = []
        for e in mg_def.get("elements") or []:
            if not isinstance(e, dict) or e.get("type") != "text":
                continue
            text = str(e.get("content") or "")
            fs = float(e.get("font_size") or 48)
            out.append({"text": text[:80], "x": e.get("x"), "y": e.get("y"),
                        "w": estimate_text_width(text, fs), "h": fs * 1.2,
                        "size": fs, "estimated": True})
        return out
