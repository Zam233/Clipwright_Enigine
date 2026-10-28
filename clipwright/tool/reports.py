"""工作区文档工具（PRD §17.5）：报告读取、报告列表、文档行级编辑。

三个都**限定在工作区内**（`assert_allowed_path` 复核），并且：

- `list_reports` / `read_report`：只读，主 agent 用它读子 agent 写的 markdown；
- `edit_file`：**只允许改 `docs/*.md`**（`reports/*` 一律拒绝——报告是 append-only 的证据）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.tool.base import BaseTool, ToolExecResult, ToolStatus

#: read_report 单次返回上限（超出提示"指定小节"，不整篇倾倒）
READ_REPORT_MAX_CHARS = 8000
#: edit_file 单次替换上限
EDIT_MAX_CHARS = 100_000


def _resolve_root(project_id: str = "", workspace_root: str = "") -> Path | None:
    """定位工作区根：显式 root 优先，其次按 project_id 推断（不存在则 None）。"""
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
        except Exception as exc:  # noqa: BLE001
            logger.debug("工作区定位失败 %s: %s", project_id, exc)
    return None


def _guard(path: Path) -> tuple[Path | None, str]:
    from clipwright.security import SecurityViolation, assert_allowed_path

    try:
        assert_allowed_path(path)
    except SecurityViolation as exc:
        return None, f"路径不在允许的目录内: {exc}"
    return path, ""


class ListReportsTool(BaseTool):
    """列出工作区里的子 agent 报告（新→旧）。"""

    name = "list_reports"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "列出工作区 reports/ 下的子 agent 汇报（seq/stage/status/attempt/headline/成本），"
        "默认最近 10 条。只读；要读正文请用 read_report。"
    )

    async def execute(
        self,
        project_id: str = "",
        workspace_root: str = "",
        limit: int = 10,
        **kwargs: Any,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        if root is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="未找到工作区（需要 project_id 或 workspace_root）")
        try:
            from clipwright.services import stage_report

            items = stage_report.list_reports(root, limit=int(limit or 10))
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"{type(exc).__name__}: {exc}")
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"workspace_root": str(root), "count": len(items), "reports": items},
        )


class ReadReportTool(BaseTool):
    """读一份报告的正文（受 max_chars 限制）。"""

    name = "read_report"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "读取工作区报告正文。参数：`seq`（报告序号）或 `path`（绝对路径），"
        "`max_chars` 默认 8000；超出时返回前 N 字并提示用 `section` 精确读取。"
    )

    async def execute(
        self,
        seq: int = 0,
        path: str = "",
        project_id: str = "",
        workspace_root: str = "",
        max_chars: int = READ_REPORT_MAX_CHARS,
        section: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        target: Path | None = None
        if path:
            target = Path(str(path))
        elif root is not None and seq:
            try:
                from clipwright.services import stage_report

                for item in stage_report.list_reports(root, limit=500):
                    if int(item.get("seq") or 0) == int(seq):
                        target = Path(str(item["path"]))
                        break
            except Exception as exc:  # noqa: BLE001
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"{type(exc).__name__}: {exc}")
        if target is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="找不到报告（给 seq 或 path）")
        guarded, err = _guard(target)
        if guarded is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        try:
            text = guarded.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))

        if section:
            from clipwright.services.stage_report import _section_text  # noqa: PLC2701

            text = _section_text(text, section) or f"（没有「{section}」这一节）"
        limit = max(200, int(max_chars or READ_REPORT_MAX_CHARS))
        truncated = len(text) > limit
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "path": str(guarded),
                "chars": len(text),
                "truncated": truncated,
                "hint": "用 section 参数精确读取某一节" if truncated else "",
                "content": text[:limit],
            },
            output_path=str(guarded),
        )


class EditFileTool(BaseTool):
    """行级替换（`old_string` 必须唯一），只允许改 `docs/*.md`。"""

    name = "edit_file"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "对工作区文档做精确替换：`old_string` 必须在文件中唯一命中，替换为 `new_string`。"
        "只允许改 `docs/*.md`（reports/ 是 append-only 证据，不可编辑）。"
    )

    async def execute(
        self,
        path: str,
        old_string: str,
        new_string: str = "",
        project_id: str = "",
        workspace_root: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        if not path:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="需要 path")
        target = Path(str(path))
        if not target.is_absolute() and root is not None:
            target = root / str(path)
        guarded, err = _guard(target)
        if guarded is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)

        # 白名单：**只允许 docs/ 下的 markdown**（PRD §17.5/§18.4）。
        # 旧实现只拒 reports/，于是 artifacts/materials/工作区根下的文本都能被改。
        parts = [p.lower() for p in guarded.parts]
        if "reports" in parts:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="报告（reports/*）是 append-only 证据，不允许编辑；要补充请让子 agent 重跑",
            )
        if "docs" not in parts or guarded.suffix.lower() != ".md":
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="edit_file 只允许修改 docs/*.md（工作区其他位置请用对应工具）",
            )
        if not guarded.is_file():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"文件不存在: {guarded}")
        if not str(old_string):
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="old_string 不能为空（不支持整文件覆盖）")

        try:
            text = guarded.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))

        hits = text.count(old_string)
        if hits == 0:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="old_string 未命中（请先 read_file 确认原文）")
        if hits > 1:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"old_string 命中 {hits} 处（必须唯一，请带更多上下文）",
            )
        updated = text.replace(old_string, str(new_string), 1)
        if len(updated) > EDIT_MAX_CHARS:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"结果过大（{len(updated)} 字），请分段修改")
        try:
            guarded.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"path": str(guarded), "chars_before": len(text), "chars_after": len(updated)},
            output_path=str(guarded),
        )


__all__ = ["EDIT_MAX_CHARS", "READ_REPORT_MAX_CHARS", "EditFileTool",
           "ListReportsTool", "ReadReportTool"]
