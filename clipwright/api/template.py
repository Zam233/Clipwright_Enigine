"""模板管理 API — 时间线模板的保存、加载、应用。

模板 = 可复用的时间线结构（轨道布局 + 动画配置 + 转场规则），
用户可以将满意的时间线保存为模板，后续一键应用到新项目。
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger
from clipwright.paths import anchor
from clipwright.security import validate_id

router = APIRouter(prefix="/api/template", tags=["template"])


async def _guard_template_id(template_id: str | None = None) -> None:
    """路由级守卫：template_id 出现在路径中时校验合法性（防路径遍历）。"""
    if template_id is not None:
        validate_id(template_id, "template_id")


router.dependencies = [Depends(_guard_template_id)]

# 模板存储目录
_TEMPLATES_DIR = anchor("templates")


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
    target_project_id: str = Field(
        default="",
        description="目标项目 ID（轮 86：提供时把模板引用的素材重映射进该项目，否则沿用原路径）",
    )


class BatchApplyRequest(BaseModel):
    """批量应用模板请求（P8: 批量选题生成）。"""
    items: list[ApplyTemplateRequest] = Field(description="批量条目（每条 topic/overrides）")


def _remap_assets_to_project(new_timeline: dict[str, Any], target_project_id: str) -> int:
    """轮 86（出片闭环）：把模板时间线引用的素材文件重映射到目标项目。

    旧实现只深拷贝时间线——clip 的 local_path/asset_id 仍指向源项目
    assets/，跨项目应用后渲染期素材缺失、静默降级色块。此处把仍存在的
    源素材文件硬链/复制进目标项目 assets/files/ 并改写引用。返回重映射
    的 clip 数。
    """
    import shutil
    from pathlib import Path as _P

    if not target_project_id:
        return 0
    try:
        from clipwright.security import validate_id as _vid
        _vid(target_project_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"非法目标项目 ID: {e}") from e

    assets_root = anchor("projects").resolve()
    remapped = 0
    for track in new_timeline.get("tracks", []) or []:
        for clip in track.get("clips", []) or []:
            meta = clip.get("metadata") or {}
            orig_local = str(meta.get("local_path") or "")
            orig_asset = str(clip.get("asset_id") or "")
            # local_path 与 asset_id 常为同一路径——去重后一次重映射两处引用
            # 轮 86：is_absolute（Windows 盘符路径不以 / 开头）
            candidates = [v for v in dict.fromkeys((orig_local, orig_asset))
                          if v and _P(v).is_absolute()]
            dests: dict[str, str] = {}
            for local in candidates:
                p = _P(local)
                try:
                    resolved = p.resolve()
                    rel = resolved.relative_to(assets_root)
                except (OSError, ValueError):
                    continue
                # 仅重映射源项目素材（<assets_root>/<src>/assets/...；
                # assets_root = anchor("projects") 已含 projects 段）
                if len(rel.parts) < 3 or rel.parts[1] != "assets":
                    continue
                src_project = rel.parts[0]
                if src_project == target_project_id or not resolved.is_file():
                    continue
                dest_dir = assets_root / target_project_id / "assets" / "files"
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{resolved.name}"
                    try:
                        os.link(str(resolved), str(dest))  # 同盘硬链（零拷贝）
                    except OSError:
                        shutil.copy2(str(resolved), str(dest))
                except OSError:
                    continue
                dests[local] = str(dest)
            if dests:
                if orig_local in dests:
                    meta["local_path"] = dests[orig_local]
                if orig_asset in dests:
                    clip["asset_id"] = dests[orig_asset]
                remapped += 1
    return remapped


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

    # 轮 86（出片闭环）：素材重映射——目标项目给定时把模板引用的素材
    # 硬链/复制进该项目，跨项目应用不再渲染期降级色块
    _remapped = 0
    if req.target_project_id:
        _remapped = _remap_assets_to_project(new_timeline, req.target_project_id)

    new_timeline["_meta"] = {
        "from_template": template_id,
        "topic": req.topic,
        "applied_at": datetime.now(tz=TIME_ZONE).isoformat(),
        **({"remapped_assets": _remapped, "target_project_id": req.target_project_id}
           if req.target_project_id else {}),
    }

    return {
        "status": "applied",
        "template_id": template_id,
        "timeline": new_timeline,
    }


@router.post("/{template_id}/batch-apply")
async def batch_apply_template(template_id: str, req: BatchApplyRequest) -> dict:
    """P8: 批量选题生成 — 一次对多个选题/覆盖参数应用同一模板，返回多条时间线副本。"""
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    if not req.items:
        return {"status": "applied", "template_id": template_id, "results": []}

    data = json.loads(path.read_text(encoding="utf-8"))
    base = data.get("timeline", {})
    results = []
    for item in req.items:
        new_timeline = copy.deepcopy(base)
        if item.overrides:
            for key, value in item.overrides.items():
                if key in new_timeline:
                    new_timeline[key] = value
        # 轮 86：批量应用同样支持素材重映射（每条目可带各自目标项目）
        _remapped = 0
        if item.target_project_id:
            _remapped = _remap_assets_to_project(new_timeline, item.target_project_id)
        new_timeline["_meta"] = {
            "from_template": template_id,
            "topic": item.topic,
            "applied_at": datetime.now(tz=TIME_ZONE).isoformat(),
            **({"remapped_assets": _remapped, "target_project_id": item.target_project_id}
               if item.target_project_id else {}),
        }
        results.append({
            "topic": item.topic,
            "timeline": new_timeline,
        })
    logger.info("批量应用模板: %s × %d", template_id, len(results))
    return {"status": "applied", "template_id": template_id, "results": results}


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
