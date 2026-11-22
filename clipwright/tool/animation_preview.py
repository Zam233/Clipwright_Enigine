"""R4：MG 单段预览（HTML 级，秒级；不取代正式 MP4 预览）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from clipwright.animation.mg_renderer import MGRenderer
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class PreviewAnimationClipTool(BaseTool):
    name = "preview_animation_clip"
    capability = "preview_animation_clip"
    permission_tier = "write_local"
    agent_callable = True
    dry_run_capable = True
    dependencies: list[str] = []
    description = "生成单段 MG 的 HTML 预览文件并返回 preview_path/html；用于挂时间线前快速看效果。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "mg_def": {"type": "object"},
            "params": {"type": "object"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "fps": {"type": "number", "default": 30},
            "workspace_root": {"type": "string"},
            "project_id": {"type": "string"},
        },
        "required": ["mg_def"],
    }

    async def execute(
        self, *, mg_def: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        width: int | None = None, height: int | None = None,
        fps: float = 30.0, workspace_root: str = "", project_id: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if not isinstance(mg_def, dict) or not mg_def:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="mg_def 不能为空")
        html = MGRenderer.render(mg_def, params=params or {}, width=width,
                                 height=height, fps=fps)
        root = Path(workspace_root) if workspace_root else None
        if root is None and project_id:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(project_id))
            root = ws.dirs().root if ws.exists else None
        preview_path = ""
        if root is not None:
            target = root / "cache" / "animation_preview" / f"anim_{uuid.uuid4().hex[:10]}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            preview_path = str(target)
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "preview_path": preview_path,
                "preview_html": html[:20000],
                "duration_sec": float(mg_def.get("duration_sec") or 0),
                "format": "html",
                "note": "HTML 预览；需要 MP4 时走 /api/animation/preview",
            },
            output_path=preview_path or None,
        )
