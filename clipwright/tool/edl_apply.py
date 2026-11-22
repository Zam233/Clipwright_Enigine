"""R3：`apply_edl` —— 批量裁剪 EDL 并落到当前时间线。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.schema.timeline import Timeline
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services import task_blackboard as bb
from clipwright.services.intent_apply import PatchOp, PatchPlan, apply_patch, validate_patch
from clipwright.tool.base import BaseTool


def _load_timeline(task_id: str) -> tuple[Timeline, str]:
    board = bb.get(task_id) or {}
    for key in ("final_timeline", "timeline"):
        raw = board.get(key)
        if raw is None:
            continue
        data = (json.loads(Path(str(raw["path"])).read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("path") else raw)
        if isinstance(data, dict):
            return Timeline(**data), key
    raise ValueError(f"任务 {task_id} 没有 timeline")


class ApplyEdlTool(BaseTool):
    name = "apply_edl"
    capability = "apply_edl"
    permission_tier = "write_local"
    agent_callable = True
    dry_run_capable = True
    dependencies = ["ffmpeg"]
    description = (
        "把 EDL 批量落到当前时间线：items[] 每项 {asset,input_path,start_sec,duration_sec,"
        "track,position}；先 video_trim_batch，再按位置新增视频 clip。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "asset": {"type": "string"},
                        "input_path": {"type": "string"},
                        "start_sec": {"type": "number"},
                        "duration_sec": {"type": "number"},
                        "track": {"type": "string"},
                        "position": {"type": "string"},
                    },
                },
            },
            "task_id": {"type": "string"},
            "dry_run": {"type": "boolean", "default": False},
            "idempotency_key": {"type": "string"},
        },
        "required": ["items", "task_id"],
    }

    async def execute(self, *, items: list[dict[str, Any]] | None = None,
                      task_id: str = "", dry_run: bool = False,
                      idempotency_key: str = "", **kwargs: Any) -> ToolExecResult:
        rows = [x for x in (items or []) if isinstance(x, dict)]
        if not rows or not task_id:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="items/task_id 不能为空")
        from clipwright.tool.registry import ToolRegistry

        edge = [{
            "asset": str(r.get("asset") or r.get("input_path") or ""),
            "start_sec": float(r.get("start_sec") or 0),
            "duration_sec": float(r.get("duration_sec") or 0),
        } for r in rows]
        trimmed = await ToolRegistry.execute(
            "video_trim_batch", items=edge, task_id=task_id,
            idempotency_key=idempotency_key,
        )
        if str(getattr(trimmed, "status", "")) not in ("success", "ok"):
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(getattr(trimmed, "error", "") or "裁剪失败"))
        out = getattr(trimmed, "output", None) or {}
        by_index = {int(x.get("index", -1)): x for x in out.get("items") or []}
        try:
            timeline, key = _load_timeline(task_id)
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))
        ops: list[PatchOp] = []
        results: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            tr = by_index.get(i) or {}
            if tr.get("status") != "ok" or not tr.get("output_path"):
                results.append({"index": i, "status": "failed",
                                "error": str(tr.get("error") or "trim failed")})
                continue
            new_clip = {
                "kind": "video",
                "asset_id": str(tr["output_path"]),
                "position": str(row.get("position") or "end"),
                "duration_sec": max(0.2, float(row.get("duration_sec") or 0)),
            }
            ops.append(PatchOp(op="add", new_clip=new_clip,
                               track_id=str(row.get("track") or ""),
                               note=f"apply_edl:{i}", source_index=i))
            results.append({"index": i, "status": "queued",
                            "output_path": str(tr["output_path"]),
                            "track": str(row.get("track") or ""),
                            "position": str(row.get("position") or "end")})
        if not ops:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="没有成功裁剪项", output={"items": results})
        plan = PatchPlan(ops=ops)
        if dry_run:
            try:
                report = validate_patch(timeline, plan)
            except Exception as exc:  # noqa: BLE001
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"预检失败: {exc}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"dry_run": True, "items": results,
                                          "report": report.model_dump(),
                                          "problems": [p.model_dump() for p in report.problems]})
        new_timeline, report = apply_patch(timeline, plan)
        if report.has_errors():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="EDL 补丁失败",
                                  output={"items": results,
                                          "problems": [p.model_dump() for p in report.problems]})
        try:
            bb.put(task_id, "timeline", new_timeline.model_dump(mode="json"), by="edit")
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"写回黑板失败: {exc}")
        added = list(report.added_clip_ids)
        queued = [x for x in results if x["status"] == "queued"]
        for pos, item in enumerate(queued):
            item["status"] = "ok"
            if pos < len(added):
                item["clip_id"] = str(added[pos])
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"items": results, "added_clip_ids": added,
                                      "timeline_key": key})
