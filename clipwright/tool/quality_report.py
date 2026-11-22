"""`quality_report` —— 读取最近一次 quality 阶段报告，供收尾前核对。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _root(project_id: str, workspace_root: str) -> Path | None:
    if workspace_root:
        p = Path(str(workspace_root)).expanduser()
        if p.is_dir():
            return p
    if project_id:
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(project_id))
            if ws.exists:
                return ws.dirs().root
        except Exception:  # noqa: BLE001
            pass
    return None


class QualityReportTool(BaseTool):
    """读最近一次 quality 阶段报告（只读）。"""

    name = "quality_report"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "读取最近一次 quality 阶段报告；没有报告时明确报错，提示先派 quality。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "max_chars": {"type": "integer", "default": 6000, "minimum": 500, "maximum": 20000},
            "project_id": {"type": "string"},
            "workspace_root": {"type": "string"},
        },
    }

    async def execute(
        self, max_chars: int = 6000,
        project_id: str = "", workspace_root: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        root = _root(project_id, workspace_root)
        if root is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="未找到工作区（需要 project_id 或 workspace_root）")
        base = root / "reports"
        candidates = sorted(
            (p for p in base.glob("*.md") if "quality" in p.name.lower()),
            key=lambda p: (p.stat().st_mtime, p.name), reverse=True,
        ) if base.is_dir() else []
        if not candidates:
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND, tool_name=self.name,
                error="没有 quality 报告；先派 quality 阶段再收尾",
            )
        path = candidates[0]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))
        limit = max(500, min(20000, int(max_chars or 6000)))
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "path": str(path), "name": path.name,
                "total_chars": len(text), "truncated": len(text) > limit,
                "content": text[:limit],
            },
            output_path=str(path),
        )
