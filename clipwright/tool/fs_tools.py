"""文件读写工具（路线 C / 主 Agent 与子代理的基础能力）。

为什么必须做：C0 的能力矩阵里，**48 个工具没有一个是"读文件/写文件"**。
也就是说主 agent 想看一眼某段字幕文本、把核查结论落成一个 md，都做不到——
它只能通过 ffmpeg 类工具间接操作媒体。这是"能调工具"和"能干活"的区别。

三条安全约束（每一条都有测试）：

1. **路径白名单**：读写都必须过 `security.assert_allowed_path`，与素材/渲染同一套根目录；
2. **体积上限**：读默认 4000 字符、最多 100KB；写最多 200KB——防止把上下文或磁盘打爆；
3. **不创建越权目录**：写文件只允许在已存在的白名单目录下写，**不自动 mkdir**（自动建目录
   等于给了"在任意位置落文件"的能力，风险大于便利）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

_READ_DEFAULT_CHARS = 4000
_READ_MAX_BYTES = 100_000
_WRITE_MAX_BYTES = 200_000


def _guarded(path: str) -> tuple[Optional[Path], Optional[str]]:
    """校验路径并返回 (Path, 错误原因)。"""
    raw = str(path or "").strip()
    if not raw:
        return None, "路径为空"
    from clipwright.security import SecurityViolation, assert_allowed_path

    p = Path(raw).expanduser()
    try:
        assert_allowed_path(p)
    except SecurityViolation as e:
        return None, f"路径不在允许的目录内: {e}"
    return p, None


class ReadFileTool(BaseTool):
    """读取一个文本文件（只读，最多 100KB）。"""

    name = "read_file"
    agent_callable = True
    dependencies: list[str] = []
    description = "读取一个文本文件的内容（默认前 4000 字符，最多 100KB）。只读，不修改任何东西。"

    async def execute(
        self,
        path: str,
        max_chars: int = _READ_DEFAULT_CHARS,
        **kwargs: Any,
    ) -> ToolExecResult:
        p, err = _guarded(path)
        if err:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        if not p.is_file():
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND, tool_name=self.name, error=f"文件不存在: {p}",
            )
        try:
            size = p.stat().st_size
            if size > _READ_MAX_BYTES:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"文件过大（{size} 字节 > {_READ_MAX_BYTES}），请改用分段读取",
                )
            limit = max(1, int(max_chars or _READ_DEFAULT_CHARS))
            text = p.read_text(encoding="utf-8", errors="replace")
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "path": str(p),
                    "size_bytes": size,
                    "truncated": len(text) > limit,
                    "content": text[:limit],
                },
            )
        except Exception as e:  # noqa: BLE001 — 读失败如实回报
            logger.warning("read_file 失败 %s: %s", p, e)
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )


class ListDirTool(BaseTool):
    """列出一个目录的条目（只读，最多 200 项）。"""

    name = "list_dir"
    agent_callable = True
    dependencies: list[str] = []
    description = "列出一个目录下的文件名与子目录（只读，最多 200 项）。用于先看清有什么再做决定。"

    async def execute(self, path: str, **kwargs: Any) -> ToolExecResult:
        p, err = _guarded(path)
        if err:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        if not p.is_dir():
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND, tool_name=self.name, error=f"目录不存在: {p}",
            )
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "path": str(p),
                    "count": len(entries),
                    "entries": [
                        {"name": e.name, "is_dir": e.is_dir()} for e in entries
                    ],
                },
            )
        except Exception as e:  # noqa: BLE001
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )


class WriteFileTool(BaseTool):
    """写一个文本文件（只允许写在白名单目录内，最多 200KB）。"""

    name = "write_file"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "把文本写入一个文件（覆盖同名文件，最多 200KB）。只能写在允许的目录内，"
        "不会自动创建缺失的父目录。用于落核查结论、生成清单、写字幕文本等。"
    )

    async def execute(
        self,
        path: str,
        content: str = "",
        append: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        p, err = _guarded(path)
        if err:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=err)
        text = str(content or "")
        data = text.encode("utf-8")
        if len(data) > _WRITE_MAX_BYTES:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"内容过大（{len(data)} 字节 > {_WRITE_MAX_BYTES}），请分段写入",
            )
        parent = p.parent
        if not parent.is_dir():
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"父目录不存在（不会自动创建）: {parent}",
            )
        # 父目录本身也要在白名单内：防止"白名单目录 → ../.. → 任意位置"
        from clipwright.security import SecurityViolation, assert_allowed_path

        try:
            assert_allowed_path(parent)
        except SecurityViolation as e:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"父目录不在允许的目录内: {e}",
            )
        try:
            before = p.stat().st_size if p.exists() else 0
            with p.open("a" if append else "w", encoding="utf-8") as fh:
                fh.write(text)
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "path": str(p),
                    "bytes_written": len(data),
                    "bytes_before": before,
                    "appended": bool(append),
                },
                output_path=str(p),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("write_file 失败 %s: %s", p, e)
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"{type(e).__name__}: {e}",
            )
