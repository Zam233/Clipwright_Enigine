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


async def repair_mg(mg_def: dict | None = None, errors: list[str] | None = None,
                    description: str = "", text_content: str = "", **_: Any) -> dict[str, Any]:
    from clipwright.animation.mg.generator import MGGenerator
    gen = MGGenerator()
    repaired = await gen._call_llm_repair(
        mg_def or {}, list(errors or [])[:10], description, text_content,
        {}, {}, {}, "",
    )
    return {"ok": bool(repaired), "mg_def": repaired or mg_def, "errors": errors or []}


async def render_preview(mg_def: dict | None = None, params: dict | None = None,
                         width: int = 1920, height: int = 1080, fps: float = 30.0, **_: Any) -> dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post("http://127.0.0.1:8080/api/animation/preview", json={
            "mg_json": mg_def or {}, "params": params or {},
            "width": width, "height": height, "fps": fps,
        })
        resp.raise_for_status()
        return resp.json()


async def research_sources(query: str = "", agent: Any = None, **_: Any) -> dict[str, Any]:
    from clipwright.animation.assets import gather_sources
    if agent is None:
        return {"sources": [], "note": "agent capability unavailable"}
    return {"sources": await gather_sources(agent, query)}


async def inspect_reference(video_path: str = "", out_dir: str = "", **_: Any) -> dict[str, Any]:
    import json
    import subprocess
    import sys
    if not video_path:
        return {"error": "video_path required"}
    script = "scripts/inspect_reference.py"
    args = [sys.executable, script, video_path]
    if out_dir:
        args += ["--out", out_dir]
    out = subprocess.check_output(args, cwd=".")
    return json.loads(out.decode("utf-8", "ignore") or "{}")


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
        "repair_mg": repair_mg,
        "render_preview": render_preview,
        "research_sources": research_sources,
        "inspect_reference": inspect_reference,
    }.items():
        bind_animation_tool(name, handler)
