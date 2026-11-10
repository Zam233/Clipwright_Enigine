"""插件请求上下文（M2）—— 只读、有上限、可截断。

给 `ClipRenderRequest` 组装 `context / cues / audio / materials / neighbors /
render_settings`。全部是 engine 已有的通用数据，不含插件领域语义；插件拿到后
自己决定怎么用（例如按 cue 时间驱动场景动画）。

安全：只返回摘要/引用，不返回本地绝对路径；materials 只读清单 + sha256。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.services.narration_events import timeline_nel_bpm
from clipwright.services.workspace import Workspace

MAX_CUES = 200
MAX_NEIGHBORS = 8
MAX_MATERIALS = 32
MAX_JSON_BYTES = 256 * 1024


def build_context(
    timeline: Any,
    clip: Any,
    *,
    task: Any = None,
    category: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    stage: str = "animation",
) -> dict[str, Any]:
    """任务/项目摘要（不含全量提示词与历史）。"""
    request = getattr(task, "request", None)
    context = {
        "topic": str(getattr(request, "topic", "") or ""),
        "category": str(getattr(request, "category_plugin_id", "") or category),
        "stage": stage,
        "duration_sec": float(getattr(timeline, "duration_sec", 0.0) or 0.0),
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
    }
    extra = getattr(request, "extra", None) or {}
    persona_id = str(getattr(request, "persona_id", "") or "")
    if persona_id:
        context["persona_id"] = persona_id
    if isinstance(extra, dict) and extra.get("brief_summary"):
        context["brief_summary"] = str(extra["brief_summary"])[:500]
    return context


def _clip_text(clip: Any) -> str:
    return str(getattr(clip, "text", "") or "")


def build_cues(timeline: Any, start: float, end: float) -> list[dict[str, Any]]:
    """字幕 cue + NEL 事件（窗口内），按时间排序，最多 MAX_CUES 条。"""
    cues: list[dict[str, Any]] = []
    for track in getattr(timeline, "tracks", []) or []:
        if str(getattr(track, "kind", "")) != "caption":
            continue
        for clip in getattr(track, "clips", []) or []:
            c_start = float(getattr(clip, "start_sec", 0.0) or 0.0)
            c_dur = float(getattr(clip, "duration_sec", 0.0) or 0.0)
            if c_start + c_dur <= start or c_start >= end:
                continue
            cues.append({
                "start_sec": round(c_start, 3),
                "duration_sec": round(c_dur, 3),
                "text": _clip_text(clip)[:200],
                "source": "caption",
            })
    nel, bpm = timeline_nel_bpm(timeline)
    for event in nel or []:
        try:
            t = float(event.get("t", 0))
        except (TypeError, ValueError):
            continue
        if not (start <= t <= end):
            continue
        cues.append({
            "start_sec": round(t, 3),
            "duration_sec": 0.0,
            "text": str(event.get("text") or event.get("payload") or "")[:200],
            "type": str(event.get("type") or ""),
            "source": "nel",
        })
    cues.sort(key=lambda c: c["start_sec"])
    if len(cues) > MAX_CUES:
        cues = cues[:MAX_CUES]
    if bpm:
        for cue in cues:
            cue.setdefault("bpm", float(bpm))
    return cues


def build_audio(timeline: Any) -> dict[str, Any] | None:
    """配音/旁白音频引用（不返回本地路径）；没有时返回 None。"""
    for track in getattr(timeline, "tracks", []) or []:
        if str(getattr(track, "kind", "")) != "audio":
            continue
        for clip in getattr(track, "clips", []) or []:
            meta = dict(getattr(clip, "metadata", {}) or {})
            if not (meta.get("dubbing") or meta.get("narration")):
                continue
            return {
                "clip_id": str(getattr(clip, "id", "")),
                "duration_sec": float(getattr(clip, "duration_sec", 0.0) or 0.0),
                "role": "dub" if meta.get("dubbing") else "narration",
                "artifact_id": str(meta.get("artifact_id") or ""),
                "source": str(meta.get("source") or ""),
            }
    return None


def build_materials(project_id: str, *, limit: int = MAX_MATERIALS) -> list[dict[str, Any]]:
    """`<project>/materials/` 的只读清单（名称/MIME/大小/sha256）。"""
    if not project_id:
        return []
    try:
        root = Workspace(project_id).dirs().materials
    except Exception as e:  # noqa: BLE001 — 工作区不存在时返回空
        logger.debug("materials 定位失败 %s: %s", project_id, e)
        return []
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(items) >= limit:
            break
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
        except OSError:
            continue
        mime, _ = mimetypes.guess_type(str(path))
        items.append({
            "name": path.name,
            "ref": f"materials/{path.relative_to(root).as_posix()}",
            "mime": mime or "application/octet-stream",
            "size": len(data),
            "sha256": digest,
        })
    return items


def _is_visual_kind(kind: object) -> bool:
    text = str(kind or "")
    if text in ("video", "image"):
        return True
    try:
        from clipwright.services.clip_kind_registry import ClipKindRegistry

        return ClipKindRegistry.is_plugin_kind(text)
    except Exception:  # noqa: BLE001
        return False


def build_neighbors(timeline: Any, clip: Any, *, radius: int = 2) -> list[dict[str, Any]]:
    """当前 clip 前后各 radius 个 clip 的只读摘要（按开始时间排序）。"""
    ordered: list[Any] = []
    for track in getattr(timeline, "tracks", []) or []:
        for item in getattr(track, "clips", []) or []:
            if _is_visual_kind(getattr(item, "kind", "")):
                ordered.append(item)
    ordered.sort(key=lambda c: float(getattr(c, "start_sec", 0.0) or 0.0))
    ids = [id(c) for c in ordered]
    try:
        idx = ids.index(id(clip))
    except ValueError:
        return []
    lo, hi = max(0, idx - radius), min(len(ordered), idx + radius + 1)
    out: list[dict[str, Any]] = []
    for item in ordered[lo:hi]:
        if item is clip:
            continue
        out.append({
            "clip_id": str(getattr(item, "id", "")),
            "kind": str(getattr(item, "kind", "")),
            "start_sec": round(float(getattr(item, "start_sec", 0.0) or 0.0), 3),
            "duration_sec": round(float(getattr(item, "duration_sec", 0.0) or 0.0), 3),
            "text": _clip_text(item)[:80],
        })
        if len(out) >= MAX_NEIGHBORS:
            break
    return out


def assemble(
    timeline: Any,
    clip: Any,
    *,
    task: Any = None,
    project_id: str = "",
    category: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    render_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次性组装 v2 请求的只读上下文；超限时截断并标记。"""
    start = float(getattr(clip, "start_sec", 0.0) or 0.0)
    duration = float(getattr(clip, "duration_sec", 0.0) or 0.0)
    payload = {
        "context": build_context(
            timeline, clip, task=task, category=category,
            width=width, height=height, fps=fps,
        ),
        "cues": build_cues(timeline, start, start + duration),
        "audio": build_audio(timeline),
        "materials": build_materials(project_id),
        "neighbors": build_neighbors(timeline, clip),
        "render_settings": dict(render_settings or {
            "width": int(width), "height": int(height), "fps": float(fps),
        }),
        "output": {"container": "mov", "alpha": True},
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        # 逐项收缩：先砍 materials/neighbors，再砍 cues 尾部
        payload["materials"] = payload["materials"][:8]
        payload["neighbors"] = payload["neighbors"][:4]
        payload["cues"] = payload["cues"][:50]
        payload["truncated"] = True
    return payload


__all__ = [
    "assemble", "build_audio", "build_context", "build_cues",
    "build_materials", "build_neighbors", "MAX_CUES", "MAX_JSON_BYTES",
]
