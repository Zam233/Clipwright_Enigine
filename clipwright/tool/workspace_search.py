"""工作区查询工具：glob / tree / 分段读取。

为什么需要它们：长任务里主 Agent 经常靠反复 `list_dir` 试探路径，或因为
`read_file` 的 100KB 上限读不了大报告。三个工具都只读、受限在白名单内，
返回有界结果，不改变工作区。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.fs_tools import _guarded, _workspace_base

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}


def _tree_items(
    root: Path, *, depth: int, pattern: str, max_entries: int,
) -> list[dict[str, Any]]:
    """有界目录树（深度优先，目录先于其子项）。"""
    out: list[dict[str, Any]] = []

    def walk(directory: Path, level: int) -> None:
        if len(out) >= max_entries:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if len(out) >= max_entries:
                return
            if entry.name in _SKIP_DIRS:
                continue
            rel = entry.relative_to(root).as_posix()
            if pattern and not fnmatch.fnmatch(rel, pattern):
                # 目录名可能命中前缀，继续下钻；文件不命中则跳过
                if not entry.is_dir() or not fnmatch.fnmatch(rel + "/", pattern):
                    if entry.is_dir() and level < depth:
                        walk(entry, level + 1)
                    continue
            out.append({
                "path": rel,
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size_bytes": None if entry.is_dir() else _safe_size(entry),
            })
            if entry.is_dir() and level < depth:
                walk(entry, level + 1)

    walk(root, 1)
    return out


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _checked_root(workspace_root: str, project_id: str) -> tuple[Path | None, str]:
    root = _workspace_base(workspace_root, project_id)
    if root is None:
        return None, "未找到工作区（需要 workspace_root 或 project_id）"
    from clipwright.security import SecurityViolation, assert_allowed_path

    try:
        assert_allowed_path(root)
    except SecurityViolation as exc:
        return None, f"工作区不在允许目录内: {exc}"
    return root, ""


class GlobWorkspaceTool(BaseTool):
    """按 glob 在工作区内查找文件。"""

    name = "glob_workspace"
    permission_tier = "read"
    agent_callable = True
    description = "在工作区内按 glob 模式查找文件（如 artifacts/*.json、reports/**/*.md），返回相对路径。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "相对工作区的 glob 模式"},
            "max_results": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            "cursor": {"type": "integer", "default": 0, "minimum": 0},
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
        },
        "required": ["pattern"],
    }

    async def execute(
        self, pattern: str = "", max_results: int = 100, cursor: int = 0,
        workspace_root: str = "", project_id: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        root, err = _checked_root(workspace_root, project_id)
        if root is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name, error=err,
            )
        pat = str(pattern or "").strip()
        if not pat or Path(pat).is_absolute() or ".." in Path(pat).parts:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="pattern 必须是非空相对工作区路径，且不能包含 ..",
            )
        cap = max(1, min(500, int(max_results or 100)))
        matches: list[dict[str, Any]] = []
        for p in root.glob(pat):
            if len(matches) >= 500:
                break
            if p.name in _SKIP_DIRS:
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            matches.append({
                "path": rel,
                "type": "dir" if p.is_dir() else "file",
                "size_bytes": None if p.is_dir() else _safe_size(p),
            })
        start = max(0, int(cursor or 0))
        page = matches[start:start + cap]
        next_cursor = start + cap if start + cap < len(matches) else 0
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"pattern": pat, "total": len(matches), "count": len(page),
                    "items": page, "next_cursor": next_cursor,
                    "truncated": next_cursor != 0},
        )


class WorkspaceTreeTool(BaseTool):
    """有界目录树摘要。"""

    name = "workspace_tree"
    permission_tier = "read"
    agent_callable = True
    description = "列出工作区目录树（有界深度与条目数），用于快速定位产物与素材。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5},
            "pattern": {"type": "string", "description": "可选：只保留匹配的相对路径 glob"},
            "max_entries": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000},
            "cursor": {"type": "integer", "default": 0, "minimum": 0},
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
        },
    }

    async def execute(
        self, depth: int = 2, pattern: str = "", max_entries: int = 200,
        cursor: int = 0, workspace_root: str = "", project_id: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        root = _workspace_base(workspace_root, project_id)
        if root is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="未找到工作区（需要 workspace_root 或 project_id）",
            )
        cap = max(1, min(1000, int(max_entries or 200)))
        items = _tree_items(
            root, depth=max(1, min(5, int(depth or 2))),
            pattern=str(pattern or "").strip(), max_entries=1000,
        )
        start = max(0, int(cursor or 0))
        page = items[start:start + cap]
        next_cursor = start + cap if start + cap < len(items) else 0
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"root": str(root), "depth": depth, "total": len(items),
                    "count": len(page), "items": page, "next_cursor": next_cursor},
        )


class ReadFileRangeTool(BaseTool):
    """分段读取大文件（替代 100KB 上限失败）。"""

    name = "read_file_range"
    permission_tier = "read"
    agent_callable = True
    description = "分段读取工作区文本文件：offset 字节 + length 字节，支持大报告分页。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对工作区）"},
            "offset": {"type": "integer", "default": 0, "minimum": 0},
            "length": {"type": "integer", "default": 4000, "minimum": 1, "maximum": 100000},
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
        },
        "required": ["path"],
    }

    async def execute(
        self, path: str = "", offset: int = 0, length: int = 4000,
        workspace_root: str = "", project_id: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        target, err = _guarded(path, _workspace_base(workspace_root, project_id))
        if err:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        if not target.is_file():
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND, tool_name=self.name,
                error=f"文件不存在: {target}",
            )
        start = max(0, int(offset or 0))
        size = max(1, min(100000, int(length or 4000)))
        try:
            total = target.stat().st_size
            with target.open("rb") as fh:
                fh.seek(start)
                raw = fh.read(size)
            text = raw.decode("utf-8", errors="replace")
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={
                    "path": str(target), "offset": start, "length": len(raw),
                    "total_bytes": total, "content": text,
                    "truncated": start + len(raw) < total,
                    "next_offset": start + len(raw) if start + len(raw) < total else None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )


class GrepWorkspaceTool(BaseTool):
    """在工作区文本文件中按正则搜索，返回行级命中。"""

    name = "grep_workspace"
    permission_tier = "read"
    agent_callable = True
    description = "在工作区文本文件中按正则搜索（可限定子目录/glob），返回 path/line/text，最多 100 条。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "default": ".", "description": "相对工作区的子目录或文件"},
            "glob": {"type": "string", "description": "可选：文件名过滤，如 *.md"},
            "ignore_case": {"type": "boolean", "default": False},
            "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
            "cursor": {"type": "integer", "default": 0, "minimum": 0},
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
        },
        "required": ["pattern"],
    }

    async def execute(
        self, pattern: str = "", path: str = ".", glob: str = "",
        ignore_case: bool = False, max_results: int = 50, cursor: int = 0,
        workspace_root: str = "", project_id: str = "", **kwargs: Any,
    ) -> ToolExecResult:
        root, err = _checked_root(workspace_root, project_id)
        if root is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        import re

        try:
            rx = re.compile(str(pattern or ""), re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"正则非法: {exc}")
        base, perr = _guarded(path or ".", root)
        if perr:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=perr)
        if base is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="定位工作区路径失败")
        cap = max(1, min(100, int(max_results or 50)))
        matches: list[dict[str, Any]] = []
        files = [base] if base.is_file() else [
            p for p in base.rglob("*")
            if p.is_file() and (not glob or fnmatch.fnmatch(p.name, glob))
        ]
        for file in files:
            if len(matches) >= 500:
                break
            if any(part in _SKIP_DIRS for part in file.parts):
                continue
            if file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mp3", ".wav"}:
                continue
            try:
                if file.stat().st_size > 2_000_000:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append({
                        "path": file.relative_to(root).as_posix(),
                        "line": lineno, "text": line[:500],
                    })
                    if len(matches) >= cap:
                        break
        start = max(0, int(cursor or 0))
        page = matches[start:start + cap]
        next_cursor = start + cap if start + cap < len(matches) else 0
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"pattern": pattern, "total": len(matches), "count": len(page),
                    "items": page, "next_cursor": next_cursor,
                    "truncated": next_cursor != 0},
        )
