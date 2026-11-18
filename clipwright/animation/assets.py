"""Plugin-facing animation assets/evidence helpers (no provider hardcoding)."""
from __future__ import annotations

from typing import Any

from clipwright.animation.brief import AnimationBrief


def build_image_prompt(brief: AnimationBrief, subject: str) -> str:
    """Mode C prompt for gpt-image-2 / host image tool. No API key needed."""
    return (
        f"16:9 motion-graphics still for: {brief.topic}. Subject: {subject}. "
        f"Style: {brief.style_id}; palette: {', '.join(brief.palette[:3])}; "
        "single strong composition, generous negative space, no generic neon gradient, "
        "no particle cloud, no watermark, no text unless explicitly requested."
    )


async def gather_sources(agent: Any, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Ask the ability layer for evidence. Missing capability degrades to []."""
    results: list[dict[str, Any]] = []
    for capability in ("kb_search", "web_search"):
        try:
            res = await agent.call_tool(capability, query=query, limit=limit)
        except Exception:
            continue
        if str(getattr(res, "status", "")) not in ("success", "ok"):
            continue
        payload = getattr(res, "output", None) or {}
        items = payload.get("results") if isinstance(payload, dict) else payload
        if isinstance(items, list):
            for item in items[:limit]:
                if isinstance(item, dict):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", ""),
                        "source": capability,
                    })
        if results:
            break
    return results
