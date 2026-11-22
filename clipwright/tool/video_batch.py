"""`video_trim_batch` —— 一批裁剪任务一次提交。

Engine 侧只做并发、逐项失败隔离与结果映射；实际裁剪仍走 `video_trim` 能力
（内置 ffmpeg 实现或插件接管者）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.capabilities import resolve
from clipwright.services import tool_jobs


class VideoTrimBatchTool(BaseTool):
    """批量视频裁剪；单批最多 50 项。"""

    name = "video_trim_batch"
    capability = "video_trim_batch"
    capability_priority = 10
    permission_tier = "write_local"
    agent_callable = True
    dry_run_capable = False
    dependencies = ["ffmpeg"]
    description = (
        "批量裁剪视频：items[] 每项 {input_path,start_sec,duration_sec/end_sec,output_path?}；"
        "逐项返回 output_path/error，单批最多 50 项。"
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
                        "input_path": {"type": "string"},
                        "start_sec": {"type": "number", "default": 0},
                        "duration_sec": {"type": "number"},
                        "end_sec": {"type": "number"},
                        "output_path": {"type": "string"},
                    },
                    "required": ["input_path"],
                },
            },
            "concurrency": {"type": "integer", "default": 2, "minimum": 1, "maximum": 6},
        },
        "required": ["items"],
    }

    def is_available(self) -> bool:
        return resolve("video_trim") is not None

    async def execute(
        self, *, items: list[dict[str, Any]] | None = None,
        concurrency: int = 2, **kwargs: Any,
    ) -> ToolExecResult:
        rows = [x for x in (items or []) if isinstance(x, dict)]
        if not rows:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="items 不能为空")
        if len(rows) > 50:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"items 最多 50 项（收到 {len(rows)}）")
        trimmer = resolve("video_trim")
        if trimmer is None:
            return ToolExecResult(status=ToolStatus.NOT_FOUND, tool_name=self.name,
                                  error="video_trim 能力当前无可用实现")
        sem = asyncio.Semaphore(max(1, min(6, int(concurrency or 2))))

        async def _one(index: int, row: dict[str, Any]) -> dict[str, Any]:
            started = time.monotonic()
            if tool_jobs.is_cancelled(job.job_id):
                return {"index": index, "status": "skipped", "error": "cancelled"}
            async with sem:
                try:
                    res = await trimmer.execute(
                        input_path=str(row.get("input_path") or ""),
                        start_sec=float(row.get("start_sec") or 0.0),
                        duration_sec=(float(row["duration_sec"])
                                      if row.get("duration_sec") is not None else None),
                        end_sec=(float(row["end_sec"])
                                 if row.get("end_sec") is not None else None),
                        output_path=str(row.get("output_path") or "") or None,
                    )
                    ok = str(getattr(res, "status", "")) in ("success", "ok")
                    return {
                        "index": index,
                        "status": "ok" if ok else "failed",
                        "output_path": str(getattr(res, "output_path", "") or ""),
                        "error": "" if ok else str(getattr(res, "error", "") or "trim failed")[:300],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                except Exception as exc:  # noqa: BLE001 — 单项失败隔离
                    return {
                        "index": index, "status": "failed",
                        "output_path": "", "error": f"{type(exc).__name__}: {exc}"[:300],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }

        job = tool_jobs.create("video_trim_batch", len(rows))
        progress = {"ok": 0, "failed": 0}

        async def _tracked(index: int, row: dict[str, Any]) -> dict[str, Any]:
            result = await _one(index, row)
            if result.get("status") == "ok":
                progress["ok"] += 1
            else:
                progress["failed"] += 1
            tool_jobs.update(job.job_id, completed=progress["ok"],
                             failed=progress["failed"])
            return result

        results = await asyncio.gather(*[_tracked(i, r) for i, r in enumerate(rows)])
        ok_count = sum(1 for x in results if x["status"] == "ok")
        tool_jobs.finish(job.job_id, failed=len(results) - ok_count,
                         error="" if ok_count else "所有裁剪任务均失败")
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok_count else ToolStatus.ERROR,
            tool_name=self.name,
            error="" if ok_count else "所有裁剪任务均失败",
            output={
                "job_id": job.job_id,
                "items": results,
                "summary": {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count},
            },
        )
