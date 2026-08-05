"""模板 API — 创建/管理视频模板 + 批量生成。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.services.template import VideoTemplate, extract_variables, batch_generate, IntroOutroConfig
from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.schema.pipeline import PipelineRequest

router = APIRouter(prefix="/api/template", tags=["template"])
_orchestrator = PipelineOrchestrator()


@router.post("/create")
async def create_template(template: VideoTemplate) -> dict:
    existing = VideoTemplate.load(template.template_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Template '{template.template_id}' already exists")
    template.save()
    return {"status": "created", "template_id": template.template_id}


@router.put("/update/{template_id}")
async def update_template(template_id: str, data: dict[str, Any]) -> dict:
    existing = VideoTemplate.load(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    updated = VideoTemplate.from_dict({**existing.to_dict(), **data, "template_id": template_id})
    updated.save()
    return {"status": "updated", "template_id": template_id}


@router.get("/list")
async def list_templates() -> list[dict[str, Any]]:
    return [t.to_dict() for t in VideoTemplate.list_all()]


@router.get("/get/{template_id}")
async def get_template(template_id: str) -> dict:
    t = VideoTemplate.load(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return t.to_dict()


@router.delete("/delete/{template_id}")
async def delete_template(template_id: str) -> dict:
    ok = VideoTemplate.delete(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {"status": "deleted"}


@router.get("/variables/{template_id}")
async def get_template_variables(template_id: str) -> dict:
    t = VideoTemplate.load(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    all_text = f"{t.topic_template} {t.script_template} {json.dumps(t.extra_params)}"
    vars_found = extract_variables(all_text)
    return {"template_id": template_id, "variables": list(set(vars_found))}


@router.post("/render/{template_id}")
async def render_template(template_id: str, variables: dict[str, str]) -> dict:
    """渲染模板并返回管线请求参数。"""
    t = VideoTemplate.load(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    result = t.render(variables)
    return {"template_id": template_id, "rendered": result}


@router.post("/batch/{template_id}")
async def batch_render(template_id: str, variables_list: list[dict[str, str]]) -> list[dict]:
    """批量渲染模板：对每组变量生成一个管线请求。"""
    try:
        results = batch_generate(template_id, variables_list)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [{"index": i, **r} for i, r in enumerate(results)]


# ── 片头/片尾 ──

@router.post("/intro-outro/create")
async def create_intro_outro(config: dict) -> dict:
    cfg = IntroOutroConfig.from_dict(config)
    cfg.save()
    return {"status": "created", "name": cfg.name}


@router.get("/intro-outro/list")
async def list_intro_outro() -> list[dict]:
    return [c.to_dict() for c in IntroOutroConfig.list_all()]


@router.delete("/intro-outro/delete/{name}")
async def delete_intro_outro(name: str) -> dict:
    ok = IntroOutroConfig.delete(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Intro/outro '{name}' not found")
    return {"status": "deleted"}


@router.post("/run/{template_id}")
async def run_template_pipeline(template_id: str, variables: dict[str, str]) -> dict:
    """渲染模板并立即执行管线。"""
    t = VideoTemplate.load(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    rendered = t.render(variables)
    request = PipelineRequest(
        persona_id=rendered["persona_id"],
        category_plugin_id=rendered["category_plugin_id"],
        topic=rendered["topic"],
        extra_params=rendered["extra_params"],
    )
    state = await _orchestrator.run(request)
    return {
        "template_id": template_id,
        "pipeline_id": state.pipeline_id,
        "status": state.status.value,
        "error": state.error,
    }
