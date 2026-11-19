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
        from clipwright.config import settings
        base = f"http://127.0.0.1:{getattr(settings, 'port', 8080)}"
        resp = await client.post(f"{base}/api/animation/preview", json={
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
                      scene_context: dict | None = None, category_context: dict | None = None,
                      vision_prompt: str = "", web_context: str = "",
                      allow_opaque_background: bool = False, background_reason: str = "",
                      width: int = 1920, height: int = 1080, fps: float = 30.0, **_: Any) -> dict[str, Any]:
    from clipwright.animation.mg.generator import MGGenerator
    gen = MGGenerator()
    last: dict[str, Any] = {}
    for _ in range(2):
        last = await gen.generate(
            description=description, text_content=text_content,
            persona_style=persona_style or {}, scene_context=scene_context or {},
            category_context=category_context or {}, vision_prompt=vision_prompt,
            web_context=web_context, width=width, height=height, fps=fps,
        )
        if last.get("success") and last.get("mg_def"):
            from clipwright.animation.agentic.adapters import failures_from_mg_def
            fs = failures_from_mg_def(last["mg_def"])
            if fs:
                last["success"] = False
                last["qa_errors"] = [f.observed for f in fs]
                last["error"] = "keyframes 含静态字段 x/y/content"
            last["background_decision"] = {
                "mode": "opaque" if allow_opaque_background else "transparent",
                "reason": str(background_reason or ""),
                "value": str((last["mg_def"].get("style") or {}).get("background") or "transparent"),
            }
            return last
    return last




async def preview_html(mg_def: dict | None = None, params: dict | None = None,
                       width: int = 1920, height: int = 1080, fps: float = 30.0, **_: Any) -> dict[str, Any]:
    from clipwright.animation.agentic.frames import write_preview_html
    path = write_preview_html(mg_def or {}, params or {}, width, height, fps)
    return {"success": True, "path": str(path), "html": str(path), "render": False}


async def capture_preview_frames(html_path: str = "", times: list[float] | None = None,
                                 background: str = "#111111", **_: Any) -> dict[str, Any]:
    import asyncio
    from clipwright.animation.agentic.frames import capture_frames
    from pathlib import Path
    frames = await asyncio.to_thread(capture_frames, Path(html_path),
                                     list(times or [0, 0.25, 0.5, 0.75, 1.0]), None, background)
    return {"success": True, "frames": [str(f) for f in frames]}


async def inspect_frames(frames: list[str] | None = None, brief: str = "", **_: Any) -> dict[str, Any]:
    from pathlib import Path
    from clipwright.animation.agentic.frames import inspect_frames as _inspect
    result = await _inspect([Path(f) for f in (frames or [])], brief)
    findings = result.get("findings") or []
    blocking = [f for f in findings if str(f.get("severity") or "") == "blocking"]
    result["success"] = not blocking
    result["qa_errors"] = [str(f.get("observation") or f) for f in blocking]
    return result


async def revise_mg(mg_def: dict | None = None, findings: list[dict] | None = None,
                    brief: str = "", **_: Any) -> dict[str, Any]:
    import json
    from clipwright.animation.mg.generator import MGGenerator
    from clipwright.services.llm import LLMService
    from clipwright.animation.mg.validator import validate_mg_json

    system = ("你是 MG 动画修复器。根据视觉质检 findings 修改完整 mg_def。"
              "只输出完整合法 JSON；必须保留 animation_id/duration/elements/params/style。"
              "不要输出解释、markdown、diff。视觉问题优先用设计手段解决，不要只改一个字段。"
              "elements[].type 只能是 text/shape/line/circle/ring/arc/bg/image；矩形必须是 type=shape, shape=rect，不能写 type=rect。"
              "每个 element 至少 2 个 keyframes，第一帧 time=0，time 严格递增；"
              "keyframes 禁止 x/y/content，位移用 translate_x/translate_y；shape 只能是 rect/ellipse。")
    user = json.dumps({"brief": brief, "findings": findings or [], "mg_def": mg_def or {}}, ensure_ascii=False)
    resp = await LLMService().generate(messages=[{"role": "system", "content": system},
                                                 {"role": "user", "content": user}])
    raw = str(getattr(resp, "content", "") or "")
    gen = MGGenerator()
    fixed = gen._parse_llm_response(raw)
    if not fixed and findings:
        user2 = json.dumps({"brief": brief, "findings": findings[:1], "mg_def": mg_def or {}},
                           ensure_ascii=False)
        resp2 = await LLMService().generate(messages=[{"role": "system", "content": system},
                                                     {"role": "user", "content": user2}])
        fixed = gen._parse_llm_response(str(getattr(resp2, "content", "") or ""))
    if not fixed:
        return {"success": False, "repaired": False, "mg_def": mg_def, "findings": findings or [],
                "qa_errors": ["revise_mg JSON 无法解析"]}
    from clipwright.animation.mg.validator import repair_mg_json
    fixed, _fixes = repair_mg_json(fixed)
    ok, errors = validate_mg_json(fixed)
    return {"success": bool(ok), "repaired": bool(ok), "mg_def": fixed,
            "findings": findings or [], "qa_errors": [] if ok else errors}




async def workspace_tree(root: str = ".", glob: str = "**/*", max_depth: int = 6,
                         include_dirs: bool = False, **_: Any) -> dict[str, Any]:
    from clipwright.animation.agentic.workspace_tools import workspace_tree as _tree
    return _tree(root, glob, max_depth, include_dirs)


async def read_file(root: str = ".", path: str = "", mode: str = "text", **_: Any) -> dict[str, Any]:
    from clipwright.animation.agentic.workspace_tools import read_file as _read
    return _read(root, path, mode)

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
        "preview_html": preview_html,
        "capture_preview_frames": capture_preview_frames,
        "inspect_frames": inspect_frames,
        "revise_mg": revise_mg,
        "workspace_tree": workspace_tree,
        "read_file": read_file,
    }.items():
        bind_animation_tool(name, handler)
