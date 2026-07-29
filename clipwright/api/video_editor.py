"""视频编辑器后端 API — 为前端时间轴编辑器提供后端支持。

功能:
  ・时间线 CRUD（保存 / 加载 / 自动保存）
  ・操作历史（undo / redo）
  ・片段级操作（添加 / 删除 / 移动 / 分割）
  ・导出（EDL / FCPXML / JSON）
  ・预览帧截取
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger

router = APIRouter(prefix="/api/video-editor", tags=["video-editor"])

# 编辑器项目存储目录
_EDITOR_DIR = Path("editor_projects")


# ── 请求/响应模型 ──────────────────────────────


class EditorProject(BaseModel):
    """编辑器项目。"""
    project_id: str = ""
    name: str = Field(default="Untitled")
    timeline: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    version: int = 0


class SaveProjectRequest(BaseModel):
    """保存项目请求。"""
    name: str = Field(default="Untitled")
    timeline: dict[str, Any] = Field(description="完整时间线 JSON")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClipOperation(BaseModel):
    """片段操作请求。"""
    track_index: int = Field(description="轨道索引")
    clip_index: Optional[int] = Field(default=None, description="片段索引 (添加时为空)")
    clip_data: Optional[dict[str, Any]] = Field(default=None, description="片段数据")
    position_sec: Optional[float] = Field(default=None, description="目标位置(秒)")


class SplitClipRequest(BaseModel):
    """分割片段请求。"""
    track_index: int
    clip_index: int
    split_at_sec: float = Field(description="分割时间点(秒)")


class ExportRequest(BaseModel):
    """导出请求。"""
    format: str = Field(default="json", description="导出格式: json/edl/fcpxml")
    fps: float = Field(default=30.0)


# ── 内存中的 undo/redo 栈 (per project) ──────

_undo_stacks: dict[str, list[dict]] = {}  # project_id → [timeline snapshots]
_redo_stacks: dict[str, list[dict]] = {}

_MAX_HISTORY = 50


# ── API 端点 ───────────────────────────────────


@router.get("/status")
async def editor_status() -> dict:
    """编辑器服务状态。"""
    _EDITOR_DIR.mkdir(parents=True, exist_ok=True)
    project_count = len(list(_EDITOR_DIR.glob("*.json")))
    return {
        "status": "ready",
        "projects": project_count,
        "storage_dir": str(_EDITOR_DIR),
    }


@router.get("/projects")
async def list_projects() -> list[dict]:
    """列出所有编辑器项目。"""
    _EDITOR_DIR.mkdir(parents=True, exist_ok=True)
    projects: list[dict] = []
    for f in sorted(_EDITOR_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            projects.append({
                "project_id": data.get("project_id", f.stem),
                "name": data.get("name", "Untitled"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "version": data.get("version", 0),
            })
        except Exception:
            continue
    return projects


@router.post("/projects/create", response_model=EditorProject)
async def create_project(req: SaveProjectRequest) -> EditorProject:
    """创建新的编辑器项目。"""
    _EDITOR_DIR.mkdir(parents=True, exist_ok=True)

    project_id = f"ed_{uuid.uuid4().hex[:10]}"
    now = datetime.now(tz=TIME_ZONE).isoformat()

    project = {
        "project_id": project_id,
        "name": req.name,
        "timeline": req.timeline,
        "metadata": req.metadata,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }

    path = _EDITOR_DIR / f"{project_id}.json"
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    # 初始化 undo 栈
    _undo_stacks[project_id] = [copy.deepcopy(req.timeline)]
    _redo_stacks[project_id] = []

    logger.info("编辑器项目已创建: %s (%s)", project_id, req.name)
    return EditorProject(**project)


@router.get("/projects/{project_id}", response_model=EditorProject)
async def get_project(project_id: str) -> EditorProject:
    """加载编辑器项目。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return EditorProject(**data)


@router.put("/projects/{project_id}", response_model=EditorProject)
async def save_project(project_id: str, req: SaveProjectRequest) -> EditorProject:
    """保存编辑器项目（手动保存）。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    existing = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(tz=TIME_ZONE).isoformat()

    # 推入 undo 栈
    _push_undo(project_id, existing.get("timeline", {}))

    existing.update({
        "name": req.name,
        "timeline": req.timeline,
        "metadata": req.metadata,
        "updated_at": now,
        "version": existing.get("version", 0) + 1,
    })

    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return EditorProject(**existing)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> dict:
    """删除编辑器项目。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    path.unlink()
    _undo_stacks.pop(project_id, None)
    _redo_stacks.pop(project_id, None)
    return {"status": "deleted", "project_id": project_id}


# ── Undo / Redo ───────────────────────────────


@router.post("/projects/{project_id}/undo")
async def undo(project_id: str) -> dict:
    """撤销上一步操作。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    stack = _undo_stacks.get(project_id, [])
    if len(stack) <= 1:
        return {"status": "no_history", "message": "没有可撤销的操作"}

    # 当前状态推入 redo
    current = stack.pop()
    _redo_stacks.setdefault(project_id, []).append(current)

    # 恢复到上一个状态
    previous = stack[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["timeline"] = copy.deepcopy(previous)
    data["updated_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"status": "ok", "timeline": previous, "remaining_undo": len(stack) - 1}


@router.post("/projects/{project_id}/redo")
async def redo(project_id: str) -> dict:
    """重做上一步撤销的操作。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    redo_stack = _redo_stacks.get(project_id, [])
    if not redo_stack:
        return {"status": "no_redo", "message": "没有可重做的操作"}

    restored = redo_stack.pop()
    _undo_stacks.setdefault(project_id, []).append(copy.deepcopy(restored))

    data = json.loads(path.read_text(encoding="utf-8"))
    data["timeline"] = copy.deepcopy(restored)
    data["updated_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"status": "ok", "timeline": restored, "remaining_redo": len(redo_stack)}


# ── 片段级操作 ─────────────────────────────────


@router.post("/projects/{project_id}/clips/add")
async def add_clip(project_id: str, op: ClipOperation) -> dict:
    """向指定轨道添加片段。"""
    timeline = _load_timeline(project_id)
    tracks = timeline.get("tracks", [])

    if op.track_index >= len(tracks):
        raise HTTPException(status_code=400, detail=f"Track index {op.track_index} out of range")

    _push_undo(project_id, timeline)

    clip = op.clip_data or {}
    if op.position_sec is not None:
        clip["start_sec"] = op.position_sec

    tracks[op.track_index].setdefault("clips", []).append(clip)
    _save_timeline(project_id, timeline)

    return {"status": "ok", "action": "add", "track_index": op.track_index}


@router.post("/projects/{project_id}/clips/remove")
async def remove_clip(project_id: str, op: ClipOperation) -> dict:
    """从指定轨道删除片段。"""
    timeline = _load_timeline(project_id)
    tracks = timeline.get("tracks", [])

    if op.track_index >= len(tracks):
        raise HTTPException(status_code=400, detail=f"Track index {op.track_index} out of range")
    if op.clip_index is None:
        raise HTTPException(status_code=400, detail="clip_index is required")

    clips = tracks[op.track_index].get("clips", [])
    if op.clip_index >= len(clips):
        raise HTTPException(status_code=400, detail=f"Clip index {op.clip_index} out of range")

    _push_undo(project_id, timeline)

    removed = clips.pop(op.clip_index)
    _save_timeline(project_id, timeline)

    return {"status": "ok", "action": "remove", "removed_clip": removed}


@router.post("/projects/{project_id}/clips/move")
async def move_clip(project_id: str, op: ClipOperation) -> dict:
    """移动片段到新的时间位置。"""
    timeline = _load_timeline(project_id)
    tracks = timeline.get("tracks", [])

    if op.track_index >= len(tracks):
        raise HTTPException(status_code=400, detail=f"Track index {op.track_index} out of range")
    if op.clip_index is None or op.position_sec is None:
        raise HTTPException(status_code=400, detail="clip_index and position_sec are required")

    clips = tracks[op.track_index].get("clips", [])
    if op.clip_index >= len(clips):
        raise HTTPException(status_code=400, detail=f"Clip index {op.clip_index} out of range")

    _push_undo(project_id, timeline)

    clips[op.clip_index]["start_sec"] = op.position_sec
    # 按 start_sec 重新排序
    tracks[op.track_index]["clips"] = sorted(clips, key=lambda c: c.get("start_sec", 0) if c else 0)
    _save_timeline(project_id, timeline)

    return {"status": "ok", "action": "move", "new_position_sec": op.position_sec}


@router.post("/projects/{project_id}/clips/split")
async def split_clip(project_id: str, req: SplitClipRequest) -> dict:
    """在指定时间点分割片段为两段。"""
    timeline = _load_timeline(project_id)
    tracks = timeline.get("tracks", [])

    if req.track_index >= len(tracks):
        raise HTTPException(status_code=400, detail=f"Track index {req.track_index} out of range")

    clips = tracks[req.track_index].get("clips", [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=400, detail=f"Clip index {req.clip_index} out of range")

    clip = clips[req.clip_index]
    if not clip:
        raise HTTPException(status_code=400, detail="Clip is empty")

    start = clip.get("start_sec", 0) or 0
    duration = clip.get("duration_sec", 0) or 0
    split_point = req.split_at_sec

    if split_point <= start or split_point >= start + duration:
        raise HTTPException(
            status_code=400,
            detail=f"Split point {split_point}s outside clip range [{start}, {start + duration}]",
        )

    _push_undo(project_id, timeline)

    # 前半段
    left = copy.deepcopy(clip)
    left["duration_sec"] = split_point - start

    # 后半段
    right = copy.deepcopy(clip)
    right["start_sec"] = split_point
    right["duration_sec"] = (start + duration) - split_point
    # 如果有 source_offset，调整后半段的源偏移
    if "source_offset_sec" in right:
        right["source_offset_sec"] = (right.get("source_offset_sec", 0) or 0) + (split_point - start)

    # 替换原片段
    clips[req.clip_index:req.clip_index + 1] = [left, right]
    _save_timeline(project_id, timeline)

    return {"status": "ok", "action": "split", "left": left, "right": right}


# ── 导出 ───────────────────────────────────────


@router.post("/projects/{project_id}/export")
async def export_project(project_id: str, req: ExportRequest) -> dict:
    """导出项目时间线为指定格式。"""
    timeline = _load_timeline(project_id)
    tracks = timeline.get("tracks", [])

    # 收集所有 clip
    all_clips: list[dict] = []
    for track in tracks:
        for clip in (track.get("clips") or []):
            if clip:
                all_clips.append(clip)

    if req.format == "json":
        return {"format": "json", "timeline": timeline}

    elif req.format == "edl":
        from clipwright.services.edl import to_edl
        edl_content = to_edl(all_clips, req.fps)
        return {"format": "edl", "content": edl_content}

    elif req.format == "fcpxml":
        from clipwright.services.edl import to_fcpxml
        xml_content = to_fcpxml(all_clips, timeline)
        return {"format": "fcpxml", "content": xml_content}

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")


# ── 辅助函数 ───────────────────────────────────


def _load_timeline(project_id: str) -> dict:
    """加载项目时间线。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("timeline", {})


def _save_timeline(project_id: str, timeline: dict) -> None:
    """保存时间线到项目文件。"""
    path = _EDITOR_DIR / f"{project_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["timeline"] = timeline
    data["updated_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    data["version"] = data.get("version", 0) + 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _push_undo(project_id: str, timeline: dict) -> None:
    """将当前时间线快照推入 undo 栈。"""
    stack = _undo_stacks.setdefault(project_id, [])
    stack.append(copy.deepcopy(timeline))
    if len(stack) > _MAX_HISTORY:
        stack.pop(0)
    # 新操作清空 redo 栈
    _redo_stacks[project_id] = []
