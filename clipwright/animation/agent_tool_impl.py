"""M1 handlers for animation Tool registry."""
from __future__ import annotations

from typing import Any

from clipwright.animation.agent_tools import bind_animation_tool
from clipwright.animation.brief import build_brief
from clipwright.animation.mg.semantic_layout import semantic_layout_errors
from clipwright.animation.prompting import build_animation_plan
from clipwright.animation.qa import qa_report


async def build_animation_brief(description: str = "", category_context: dict | None = None,
                                duration_sec: float = 4.0, **_: Any) -> dict[str, Any]:
    return build_brief(description, category_context, duration_sec).as_dict()


async def build_shot_plan(description: str = "", category_context: dict | None = None,
                          duration_sec: float = 4.0, **_: Any) -> dict[str, Any]:
    return build_animation_plan(description, category_context, duration_sec).as_dict()


async def build_draft_spec(description: str = "", text_content: str = "", **_: Any) -> dict[str, Any]:
    parts = [p.strip() for p in str(text_content).split("|") if p.strip()]
    return {"roles": [{"role": "hero" if i == 0 else "support", "content": p} for i, p in enumerate(parts)],
            "intent": description, "constraints": {"max_elements": 5, "duplicate_text": False}}


async def validate_layout(mg_def: dict | None = None, **_: Any) -> dict[str, Any]:
    errors = semantic_layout_errors(mg_def or {}, (mg_def or {}).get("params"))
    return {"ok": not errors, "errors": errors}


async def qa_mg(mg_def: dict | None = None, **_: Any) -> dict[str, Any]:
    return qa_report(mg_def or {})


async def generate_mg(description: str = "", text_content: str = "", persona_style: dict | None = None,
                      width: int = 1920, height: int = 1080, fps: float = 30.0, **_: Any) -> dict[str, Any]:
    from clipwright.animation.mg.generator import MGGenerator
    gen = MGGenerator()
    return await gen.generate(description=description, text_content=text_content,
                              persona_style=persona_style or {}, width=width, height=height, fps=fps)


def install_animation_tool_handlers() -> None:
    for name, handler in {
        "build_animation_brief": build_animation_brief,
        "build_shot_plan": build_shot_plan,
        "build_draft_spec": build_draft_spec,
        "generate_mg": generate_mg,
        "validate_layout": validate_layout,
        "qa_mg": qa_mg,
    }.items():
        bind_animation_tool(name, handler)
