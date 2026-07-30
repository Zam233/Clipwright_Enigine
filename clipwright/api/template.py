"""模板管理 API — 时间线模板的保存、加载、应用。

模板 = 可复用的时间线结构（轨道布局 + 动画配置 + 转场规则），
用户可以将满意的时间线保存为模板，后续一键应用到新项目。
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger
from clipwright.security import validate_id

router = APIRouter(prefix="/api/template", tags=["template"])


async def _guard_template_id(template_id: str | None = None) -> None:
    """路由级守卫：template_id 出现在路径中时校验合法性（防路径遍历）。"""
    if template_id is not None:
        validate_id(template_id, "template_id")


router.dependencies = [Depends(_guard_template_id)]

# 模板存储目录
_TEMPLATES_DIR = Path("templates")


# ── 请求/响应模型 ──────────────────────────────


class TemplateMeta(BaseModel):
    """模板元信息。"""
    template_id: str
    name: str
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    source_pipeline_id: str = ""
    track_count: int = 0
    duration_sec: float = 0


class CreateTemplateRequest(BaseModel):
    """创建模板请求。"""
    name: str = Field(description="模板名称")
    description: str = Field(default="", description="模板描述")
    category: str = Field(default="", description="分类 (如 knowledge, vlog)")
    tags: list[str] = Field(default_factory=list)
    timeline: dict[str, Any] = Field(description="时间线 JSON")
    source_pipeline_id: str = Field(default="", description="来源管线 ID")


class ApplyTemplateRequest(BaseModel):
    """应用模板请求。"""
    topic: str = Field(default="", description="新视频主题")
    overrides: dict[str, Any] = Field(default_factory=dict, description="覆盖参数")


# ── API 端点 ───────────────────────────────────


@router.get("/list", response_model=list[TemplateMeta])
async def list_templates(category: str = "", tag: str = "") -> list[TemplateMeta]:
    """列出所有模板，支持按分类和标签过滤。"""
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates: list[TemplateMeta] = []

    for f in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.get("meta", {})
            if category and meta.get("category") != category:
                continue
            if tag and tag not in meta.get("tags", []):
                continue
            templates.append(TemplateMeta(**meta))
        except Exception:
            logger.warning("Failed to parse template: %s", f, exc_info=True)
            continue

    return templates


@router.get("/{template_id}")
async def get_template(template_id: str) -> dict:
    """获取模板完整内容（含时间线）。"""
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/create", response_model=TemplateMeta)
async def create_template(req: CreateTemplateRequest) -> TemplateMeta:
    """从时间线创建模板。"""
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    template_id = f"tpl_{uuid.uuid4().hex[:10]}"
    now = datetime.now(tz=TIME_ZONE).isoformat()

    tracks = req.timeline.get("tracks", [])
    duration = _calc_duration(tracks)

    meta = {
        "template_id": template_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "tags": req.tags,
        "created_at": now,
        "updated_at": now,
        "source_pipeline_id": req.source_pipeline_id,
        "track_count": len(tracks),
        "duration_sec": round(duration, 2),
    }

    data = {"meta": meta, "timeline": req.timeline}
    path = _TEMPLATES_DIR / f"{template_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("模板已创建: %s (%s)", template_id, req.name)
    return TemplateMeta(**meta)


@router.put("/{template_id}", response_model=TemplateMeta)
async def update_template(template_id: str, req: CreateTemplateRequest) -> TemplateMeta:
    """更新模板。"""
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    existing = json.loads(path.read_text(encoding="utf-8"))
    old_meta = existing.get("meta", {})
    now = datetime.now(tz=TIME_ZONE).isoformat()

    tracks = req.timeline.get("tracks", [])
    duration = _calc_duration(tracks)

    meta = {
        "template_id": template_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "tags": req.tags,
        "created_at": old_meta.get("created_at", now),
        "updated_at": now,
        "source_pipeline_id": req.source_pipeline_id or old_meta.get("source_pipeline_id", ""),
        "track_count": len(tracks),
        "duration_sec": round(duration, 2),
    }

    data = {"meta": meta, "timeline": req.timeline}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("模板已更新: %s", template_id)
    return TemplateMeta(**meta)


@router.delete("/{template_id}")
async def delete_template(template_id: str) -> dict:
    """删除模板。"""
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    path.unlink()
    logger.info("模板已删除: %s", template_id)
    return {"status": "deleted", "template_id": template_id}


@router.post("/{template_id}/apply")
async def apply_template(template_id: str, req: ApplyTemplateRequest) -> dict:
    """应用模板 — 基于模板时间线生成新的可编辑时间线副本。"""
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    data = json.loads(path.read_text(encoding="utf-8"))
    timeline = data.get("timeline", {})

    new_timeline = copy.deepcopy(timeline)

    # 应用覆盖参数
    if req.overrides:
        for key, value in req.overrides.items():
            if key in new_timeline:
                new_timeline[key] = value

    new_timeline["_meta"] = {
        "from_template": template_id,
        "topic": req.topic,
        "applied_at": datetime.now(tz=TIME_ZONE).isoformat(),
    }

    return {
        "status": "applied",
        "template_id": template_id,
        "timeline": new_timeline,
    }


# ── 辅助函数 ───────────────────────────────────


def _calc_duration(tracks: list[dict]) -> float:
    """计算时间线总时长。"""
    duration = 0.0
    for track in tracks:
        for clip in (track.get("clips") or []):
            if clip:
                end = (clip.get("start_sec", 0) or 0) + (clip.get("duration_sec", 0) or 0)
                duration = max(duration, end)
    return duration
