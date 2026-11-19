"""Browser-based MG frame capture: no MP4 render in the agent loop."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from clipwright.animation.mg_renderer import MGRenderer


def write_preview_html(mg_def: dict[str, Any], params: dict[str, Any] | None = None,
                       width: int = 1920, height: int = 1080, fps: float = 30.0) -> Path:
    html = MGRenderer.render(mg_def, params or {}, width=width, height=height, fps=fps)
    digest = hashlib.sha256(json.dumps(mg_def, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    out = Path("renders") / "agentic-preview" / digest
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def capture_frames(html_path: Path, times: list[float], out_dir: Path | None = None,
                   background: str = "#111111") -> list[Path]:
    from playwright.sync_api import sync_playwright

    out_dir = out_dir or html_path.parent / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto(html_path.resolve().as_uri())
        # 透明 MG 的预览需要受控底色，否则白底会把浅色文字误判为不可见。
        page.add_style_tag(content=f"html,body{{background:{background} !important;}}")
        page.wait_for_function("window.__timelines && window.__timelines.main", timeout=30_000)
        for t in times:
            page.evaluate("(t) => window.__timelines.main.seek(t)", float(t))
            page.wait_for_timeout(120)
            path = out_dir / f"frame_{int(round(float(t) * 1000)):05d}.png"
            page.screenshot(path=str(path))
            frames.append(path)
        browser.close()
    return frames


def inspect_frames(frames: list[Path], brief: str = "") -> dict[str, Any]:
    """Vision inspection over captured frames. Returns structured findings."""
    import base64
    from clipwright.config import settings
    from clipwright.services.llm import LLMService

    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": ("你是动画视觉质检。检查这些帧：文字可读性、元素重叠/出画、层级、"
                 "数字单位完整性、主运动是否清楚。只输出 JSON："
                 '{"findings":[{"severity":"blocking|warning","category":"legibility|layout|hierarchy|motion|content",'
                 '"observation":"...","frame":"frame_XXXXX.png"}]}。没有问题时 findings=[]。'
                 f"\nBrief: {brief}"),
    }]
    for f in frames:
        b64 = base64.b64encode(f.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    llm = LLMService()
    import asyncio
    resp = asyncio.run(llm.generate(
        messages=[{"role": "user", "content": content}],
        model=getattr(settings, "vision_llm_model", None),
    ))
    import json
    raw = str(getattr(resp, "content", "") or "")
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1]) if start >= 0 else {"findings": [], "raw": raw}
