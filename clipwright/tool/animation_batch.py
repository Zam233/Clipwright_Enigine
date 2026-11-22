"""`generate_mg_batch` —— 一批 MG 动画一次提交。

Engine 负责并发与逐项失败；实际 MG 生成仍复用 `animation.mg.MGGenerator`。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.animation.mg.validator import validate_mg_json
from clipwright.animation.mg_renderer import MGRenderer
from clipwright.services import tool_jobs


def _preview(mg_def: dict[str, Any], workspace_root: str, project_id: str) -> dict[str, str]:
    if not mg_def:
        return {}
    try:
        html = MGRenderer.render(mg_def)
    except Exception:
        return {}
    root = Path(workspace_root) if workspace_root else None
    if root is None and project_id:
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(project_id))
            root = ws.dirs().root if ws.exists else None
        except Exception:
            root = None
    path = ""
    if root is not None:
        target = root / "cache" / "animation_preview" / f"mg_{uuid.uuid4().hex[:10]}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        path = str(target)
    return {"path": path, "html": html[:20000]}


class GenerateMGBatchTool(BaseTool):
    """批量生成 MG 动画；单批最多 12 项。"""

    name = "generate_mg_batch"
    capability = "generate_mg_batch"
    capability_priority = 10
    permission_tier = "costly"
    agent_callable = True
    dry_run_capable = False
    dependencies: list[str] = []
    description = (
        "批量生成 MG 动画：items[] 每项 {description,text_content,persona_style?,scene_context?}；"
        "逐项返回 mg_def/status/error，单批最多 12 项。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "text_content": {"type": "string"},
                        "type_id": {"type": "string"},
                        "duration_s": {"type": "number"},
                        "mg_def": {"type": "object"},
                        "persona_style": {"type": "object"},
                        "scene_context": {"type": "object"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                    "required": ["description", "text_content"],
                },
            },
            "concurrency": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "task_id": {"type": "string"},
            "workspace_root": {"type": "string"},
            "project_id": {"type": "string"},
            "pipeline_id": {"type": "string"},
        },
        "required": ["items"],
    }

    async def execute(
        self, *, items: list[dict[str, Any]] | None = None,
        concurrency: int = 2, pipeline_id: str = "", task_id: str = "",
        workspace_root: str = "", project_id: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        rows = [x for x in (items or []) if isinstance(x, dict)]
        if not rows:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="items 不能为空")
        if len(rows) > 12:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"items 最多 12 项（收到 {len(rows)}）")
        try:
            from clipwright.animation.mg.generator import MGGenerator

            gen = MGGenerator()
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.NOT_FOUND, tool_name=self.name,
                                  error=f"MG 生成器不可用: {exc}")
        sem = asyncio.Semaphore(max(1, min(4, int(concurrency or 2))))

        async def _one(index: int, row: dict[str, Any]) -> dict[str, Any]:
            started = time.monotonic()
            if tool_jobs.is_cancelled(job.job_id):
                return {"index": index, "status": "skipped", "error": "cancelled"}
            async with sem:
                try:
                    supplied = row.get("mg_def")
                    if isinstance(supplied, dict) and supplied:
                        valid, issues = validate_mg_json(supplied)
                        result = {"success": valid, "mg_def": supplied if valid else None,
                                  "issues": issues, "method": "supplied",
                                  "error": "" if valid else "; ".join(issues[:3])}
                        ok = valid
                        preview = _preview(supplied, workspace_root, project_id)
                        return {
                            "index": index, "status": "ok" if ok else "failed",
                            "mg_def": result.get("mg_def") if ok else None,
                            "html": preview.get("html", ""), "issues": issues,
                            "preview_path": preview.get("path", ""),
                            "method": "supplied",
                            "error": "" if ok else str(result.get("error") or "")[:300],
                            "duration_ms": int((time.monotonic() - started) * 1000),
                        }
                    type_id = str(row.get("type_id") or "")
                    if type_id:
                        from clipwright.animation.type_specs import get_spec

                        spec = get_spec(type_id)
                        if spec is None:
                            raise ValueError(f"未注册的动画类型: {type_id}")
                        typed = await gen.generate_for_type(
                            spec=spec,
                            content=str(row.get("text_content") or ""),
                            duration_s=float(row.get("duration_s") or 4.0),
                            persona_style=dict(row.get("persona_style") or {}),
                            scene_context=dict(row.get("scene_context") or {}),
                            width=int(row["width"]) if row.get("width") else None,
                            height=int(row["height"]) if row.get("height") else None,
                            pipeline_id=pipeline_id or None,
                        )
                        result = typed.to_dict()
                        ok = bool(result.get("ok")) and bool(result.get("mg_def"))
                    else:
                        result = await gen.generate(
                            description=str(row.get("description") or ""),
                            text_content=str(row.get("text_content") or ""),
                            persona_style=dict(row.get("persona_style") or {}),
                            scene_context=dict(row.get("scene_context") or {}),
                            pipeline_id=pipeline_id or None,
                            width=int(row["width"]) if row.get("width") else None,
                            height=int(row["height"]) if row.get("height") else None,
                        )
                        ok = bool(result.get("success", True)) and bool(result.get("mg_def"))
                    return {
                        "index": index,
                        "status": "ok" if ok else "failed",
                        "mg_def": result.get("mg_def") if ok else None,
                        "html": result.get("html", ""),
                        "issues": result.get("issues") or [],
                        "motion": result.get("motion") or {},
                        "method": result.get("method", ""),
                        "preview_path": (_preview(result.get("mg_def") or {}, workspace_root,
                                                  project_id).get("path", "") if ok else ""),
                        "duration_s": float(row.get("duration_s") or 0) or None,
                        "error": "" if ok else str(result.get("error") or "MG generation failed")[:300],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                except Exception as exc:  # noqa: BLE001 — 单项失败隔离
                    return {
                        "index": index, "status": "failed", "mg_def": None,
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }

        job = tool_jobs.create("generate_mg_batch", len(rows))
        progress = {"ok": 0, "failed": 0}

        async def _tracked(index: int, row: dict[str, Any]) -> dict[str, Any]:
            result = await _one(index, row)
            if result.get("status") == "ok":
                progress["ok"] += 1
            else:
                progress["failed"] += 1
            tool_jobs.update(job.job_id, completed=progress["ok"],
                             failed=progress["failed"])
            tool_jobs.emit_progress(task_id, job)
            return result

        results = await asyncio.gather(*[_tracked(i, r) for i, r in enumerate(rows)])
        ok_count = sum(1 for x in results if x["status"] == "ok")
        tool_jobs.finish(job.job_id, failed=len(results) - ok_count,
                         error="" if ok_count else "所有 MG 生成任务均失败")
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok_count else ToolStatus.ERROR,
            tool_name=self.name,
            error="" if ok_count else "所有 MG 生成任务均失败",
            output={
                "job_id": job.job_id,
                "items": results,
                "summary": {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count},
            },
        )
