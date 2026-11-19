"""Three style-frame contract (garden-skills checkpoint)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from clipwright.animation.brief import AnimationBrief, STYLE_LIBRARY


@dataclass
class StyleFrame:
    style_id: str
    name: str
    prompt: str
    palette: list[str]
    motion: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_style_frames(brief: AnimationBrief, variants: int = 3) -> list[StyleFrame]:
    ids = ["swiss-editorial", "cinematic-title", "data-story"]
    if brief.style_id in STYLE_LIBRARY:
        ids = [brief.style_id] + [x for x in ids if x != brief.style_id]
    frames: list[StyleFrame] = []
    for sid in ids[:max(1, variants)]:
        style = STYLE_LIBRARY[sid]
        prompt = (
            f"16:9 motion-graphics style frame for: {brief.topic}. "
            f"Style: {style.name}; signals: {', '.join(style.signals)}; "
            f"palette: {', '.join(style.palette[:3])}; typography: {style.display_type}. "
            "Single strong composition, no generic dark gradient, no particle cloud, "
            "no centered title unless the style requires it. Leave room for one hero motion."
        )
        frames.append(StyleFrame(sid, style.name, prompt, list(style.palette[:3]), style.motion_character))
    return frames
