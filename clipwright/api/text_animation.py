"""文字动画插件能力/编辑 API。

validate/compile 不写盘、不调用 LLM；author 只启动 Agent 阶段任务；
commit 由服务端重新校验/编译，不信任客户端 hash/产物。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/text-animation", tags=["text-animation"])

_SUPPORTED = {
    "text", "transform", "transform2d", "opacity", "blur", "stagger",
    "mask", "clip", "camera", "effects",
}
_AUTHOR_TASKS: dict[str, dict[str, Any]] = {}


class SpecRequest(BaseModel):
    clip_id: str = ""
    base_hash: str = ""
    schema_version: str = "motion_spec/1"
    capability_version: str = "1"
    draft_spec: dict[str, Any]


class AuthorRequest(BaseModel):
    project_id: str = ""
    clip_id: str = ""
    text: str = ""
    duration_sec: float = 2.0
    style: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""


class CommitRequest(BaseModel):
    project_id: str
    clip_id: str
    base_hash: str = ""
    spec: dict[str, Any]
    spec_hash: str = ""
    compiler_version: str = "1"
    fidelity: str = "approx"


def _canonical_hash(spec: Any) -> str:
    if hasattr(spec, "model_dump"):
        spec = spec.model_dump(by_alias=True, exclude_none=False)
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _errors_md(errors: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in errors:
        if isinstance(e, dict):
            out.append(e)
        else:
            out.append({"message": str(e)})
    return out


def _validate_and_compile(draft: dict[str, Any]) -> dict[str, Any]:
    from clipwright.animation.motion_compiler import compile_motion_spec
    from clipwright.animation.motion_spec import MotionSpec
    from clipwright.animation.motion_spec_validator import validate_motion_spec
    from clipwright.animation.mg.validator import validate_mg_json

    try:
        spec = MotionSpec.model_validate(draft)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [{"field": "spec", "message": str(exc)[:500]}]}
    validation = validate_motion_spec(spec, capabilities=_SUPPORTED)
    errors = _errors_md(validation.errors)
    if not validation.ok:
        return {"ok": False, "errors": errors, "warnings": validation.warnings}
    plan = compile_motion_spec(spec, width=int(spec.canvas.get("width", 1920)),
                               height=int(spec.canvas.get("height", 1080)))
    ok_mg, mg_errors = validate_mg_json(plan.mg_def)
    if not ok_mg:
        return {"ok": False, "errors": _errors_md(list(mg_errors))}
    return {
        "ok": True, "errors": [], "warnings": validation.warnings,
        "spec": spec, "spec_hash": _canonical_hash(spec), "compiler_version": "1",
        "mg_def": plan.mg_def, "preview_ops": plan.preview_ops,
        "fidelity": "approx",
    }


@router.get("/capabilities")
async def text_animation_capabilities() -> dict:
    from clipwright.animation.text_engine import TextAnimationEngine
    caps = TextAnimationEngine().capabilities()
    caps.update({"schema_version": "motion_spec/1", "compiler_version": "1",
                 "capability_version": "1", "features": sorted(_SUPPORTED)})
    return caps


@router.get("/style-packs")
async def text_animation_style_packs() -> dict:
    from clipwright.animation.text_plugin_registry import TextStylePackRegistry
    return {"style_packs": [
        {"id": x["id"], "name": x.get("name") or x["id"], "style": x.get("style") or {}}
        for x in TextStylePackRegistry.list()
    ]}


@router.get("/asset-packs")
async def text_animation_asset_packs() -> dict:
    from clipwright.animation.text_plugin_registry import TextAssetPackRegistry
    return {"asset_packs": [
        {"id": x["id"], "name": x.get("name") or x["id"], "assets": x.get("assets") or []}
        for x in TextAssetPackRegistry.list()
    ]}


@router.post("/validate-spec")
async def validate_spec(body: SpecRequest) -> dict:
    result = _validate_and_compile(body.draft_spec)
    return {
        "ok": bool(result.get("ok")),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "can_commit": bool(result.get("ok")),
        "capability_version": "1",
    }


@router.post("/compile")
async def compile_spec(body: SpecRequest) -> dict:
    result = _validate_and_compile(body.draft_spec)
    if not result.get("ok"):
        return {"ok": False, "errors": result.get("errors", []),
                "warnings": result.get("warnings", [])}
    return {
        "ok": True, "errors": [], "warnings": result.get("warnings", []),
        "spec_hash": result["spec_hash"], "compiler_version": "1",
        "mg_def": result["mg_def"], "preview_ops": result["preview_ops"],
        "fidelity": result["fidelity"],
    }


async def _run_author(task_id: str, body: AuthorRequest) -> None:
    task = _AUTHOR_TASKS[task_id]
    try:
        from clipwright.animation.motion_spec import MotionAuthorRequest
        from clipwright.animation.text_engine import TextMotionRegistry
        provider = TextMotionRegistry.get("llm_motion")
        if provider is None:
            raise RuntimeError("MOTION_AUTHOR_UNAVAILABLE: text-motion-llm 未加载")
        spec = await provider.author(MotionAuthorRequest(
            text=body.text, style=body.style, intent={},
            width=1920, height=1080, duration_sec=float(body.duration_sec),
            scene_context={"project_id": body.project_id},
        ))
        task.update({"status": "succeeded", "spec": spec.model_dump(by_alias=True),
                     "spec_hash": _canonical_hash(spec), "progress": 100})
    except asyncio.CancelledError:
        task.update({"status": "cancelled"})
    except Exception as exc:  # noqa: BLE001
        task.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800]})


@router.post("/author")
async def start_author(body: AuthorRequest) -> dict:
    task_id = f"ma_{uuid.uuid4().hex[:12]}"
    _AUTHOR_TASKS[task_id] = {
        "task_id": task_id, "status": "authoring", "progress": 0,
        "created_at": time.time(), "cancelable": True,
    }
    _AUTHOR_TASKS[task_id]["_task"] = asyncio.create_task(_run_author(task_id, body))
    return {"task_id": task_id, "status": "authoring", "clip_id": body.clip_id}


@router.get("/author/{task_id}")
async def author_status(task_id: str) -> dict:
    task = _AUTHOR_TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="author task not found")
    return {k: v for k, v in task.items() if not k.startswith("_")}


@router.post("/author/{task_id}/cancel")
async def cancel_author(task_id: str) -> dict:
    task = _AUTHOR_TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="author task not found")
    t = task.get("_task")
    if isinstance(t, asyncio.Task):
        t.cancel()
    task.update({"status": "cancel_requested", "cancelable": False})
    return {k: v for k, v in task.items() if not k.startswith("_")}


@router.post("/commit")
async def commit_spec(body: CommitRequest) -> dict:
    from clipwright.services.project_manager import ProjectManager

    manager = ProjectManager()
    project = manager.load(body.project_id) if hasattr(manager, "load") else None
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    timeline = project.get("timeline") or {}
    clip = None
    for track in timeline.get("tracks") or []:
        for c in track.get("clips") or []:
            if str(c.get("id")) == str(body.clip_id):
                clip = c
                break
        if clip:
            break
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if str(clip.get("kind")) not in ("text", "caption"):
        raise HTTPException(status_code=400, detail="clip is not text")
    meta = dict(clip.get("metadata") or {})
    current_hash = str(meta.get("motion_spec_hash") or "")
    if body.base_hash and current_hash and body.base_hash != current_hash:
        raise HTTPException(status_code=409, detail={
            "code": "MOTION_COMMIT_CONFLICT", "current_hash": current_hash,
        })
    result = _validate_and_compile(body.spec)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("errors", []))
    from clipwright.animation.mg_renderer import MGRenderer
    mg_def = result["mg_def"]
    mg_html = MGRenderer.render(mg_def, params={}, width=int(mg_def.get("width", 1920)),
                                height=int(mg_def.get("height", 1080)), fps=30)
    meta.update({
        "motion_spec": result["spec"].model_dump(by_alias=True),
        "motion_spec_hash": result["spec_hash"],
        "motion_spec_version": "motion_spec/1",
        "motion_spec_compiler_version": "1",
        "mg_def": mg_def,
        "mg_html": mg_html,
        "preview_ops": result["preview_ops"],
        "fidelity": result["fidelity"],
        "text_provider_id": "motion_spec",
        "apply_text_style": True,
    })
    clip["metadata"] = meta
    manager.save(body.project_id, {"timeline": timeline})
    return {"ok": True, "spec_hash": result["spec_hash"],
            "revision": str(project.get("updated_at") or "")}
