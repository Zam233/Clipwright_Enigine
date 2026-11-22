"""附加：时间线探查（场景实际用了什么素材/落点）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.schema.timeline import Timeline
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services import task_blackboard as bb
from clipwright.tool.base import BaseTool


def _timeline(task_id: str = "", timeline: dict[str, Any] | Timeline | None = None) -> Timeline:
    if isinstance(timeline, Timeline):
        return timeline
    if isinstance(timeline, dict):
        return Timeline(**timeline)
    board = bb.get(task_id) or {}
    for key in ("final_timeline", "timeline"):
        raw = board.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict) and raw.get("path"):
            raw = json.loads(Path(str(raw["path"])).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return Timeline(**raw)
    raise ValueError(f"任务 {task_id} 没有 timeline")


class TimelineProbeTool(BaseTool):
    name = "timeline_probe"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "查看时间线某场景/轨道实际使用的素材、起点、时长；只读。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "timeline": {"type": "object"},
            "track_id": {"type": "string"},
            "scene_index": {"type": "integer"},
            "limit": {"type": "integer", "default": 200},
        },
    }

    async def execute(self, *, task_id: str = "",
                      timeline: dict[str, Any] | None = None,
                      track_id: str = "", scene_index: int = -1,
                      limit: int = 200, **kwargs: Any) -> ToolExecResult:
        try:
            tl = _timeline(task_id, timeline)
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))
        rows: list[dict[str, Any]] = []
        for track in tl.tracks:
            if track_id and str(track.id) != track_id:
                continue
            for clip in track.clips or []:
                meta = clip.metadata or {}
                if scene_index >= 0 and meta.get("scene_index") != scene_index:
                    continue
                rows.append({
                    "clip_id": str(clip.id), "track_id": str(track.id),
                    "kind": str(clip.kind), "asset_id": str(clip.asset_id),
                    "start_sec": float(clip.start_sec), "duration_sec": float(clip.duration_sec),
                    "scene_index": meta.get("scene_index"),
                    "label": meta.get("label") or meta.get("source_title") or "",
                })
                if len(rows) >= max(1, int(limit or 200)):
                    break
            if len(rows) >= max(1, int(limit or 200)):
                break
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"items": rows, "total": len(rows),
                                      "duration_sec": float(tl.duration_sec or 0)})
