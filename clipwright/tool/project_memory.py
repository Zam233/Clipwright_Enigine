"""项目决策记忆工具（V8）——把 `memory.md` 四段式暴露成原子工具。

为什么做成 Tool
--------------
"当时的判断与理由"必须能**被 Agent 自己记账**，否则四段式只是一个人工文档。
本工具给 Agent 两个动作：
- `read`：读回四段式记忆（含紧凑摘要，可直接拼进提示词）；
- `append`：把本次的关键决策/待办追加进对应段落（**追加不覆盖**，保住人工编辑）。

让 Agent 记账的意义：结构 Agent 记下"为什么这样分场"，动画 Agent 记下
"为什么这个动效用 waist"，质检 Agent 记下"哪一处已知风险未解决"——
下次重跑同项目时，记忆先于时间线被读入，避免同一个错误反复出现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class ProjectMemoryTool(BaseTool):
    """读写项目四段式决策记忆（Strategy / Decisions / Reasoning / Outstanding）。"""

    name = "project_memory"
    description = (
        "读写项目决策记忆 memory.md（四段式：Strategy 策略 / Decisions 决策 / "
        "Reasoning 理由 / Outstanding 待办）。action=read 读回记忆与摘要；"
        "action=append 追加条目（不覆盖人工编辑）"
    )
    dependencies = []

    async def execute(
        self,
        action: str = "read",
        *,
        project_id: str = "",
        project_dir: str = "",
        entries: Optional[Any] = None,
        source: str = "",
        limit: int = 5,
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services.project_memory import (
            SECTIONS, append_entries, load_memory, render_summary,
        )

        target = self._resolve_dir(project_id, project_dir)
        if target is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="需要 project_id 或 project_dir 之一（且项目目录存在）")

        act = (action or "read").strip().lower()
        try:
            if act == "read":
                memory = load_memory(target)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
                    "action": "read",
                    "dir": str(target),
                    "empty": memory.is_empty(),
                    "count": memory.count(),
                    "sections": {s: [e.text for e in (memory.entries.get(s) or [])] for s in SECTIONS},
                    "summary": render_summary(memory, limit=max(1, int(limit or 5))),
                })

            if act in ("append", "write"):
                items = self._normalize_entries(entries)
                if not items:
                    return ToolExecResult(
                        status=ToolStatus.ERROR, tool_name=self.name,
                        error="append 需要 entries（{段名: [文本]} 或 [[段名, 文本]]）")
                memory = append_entries(target, items, source=source)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
                    "action": "append",
                    "dir": str(target),
                    "appended": len(items),
                    "count": memory.count(),
                    "sections": {s: [e.text for e in (memory.entries.get(s) or [])] for s in SECTIONS},
                })

            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"未知 action: {action!r}（可用：read / append）")
        except ValueError as exc:  # 段名非法等调用方错误
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))
        except Exception as exc:
            logger.warning("project_memory 工具失败: %s", exc)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))

    # ── helpers ──

    @staticmethod
    def _resolve_dir(project_id: str, project_dir: str) -> Optional[Path]:
        """确定项目目录：优先显式 project_dir，其次按 project_id 查 ProjectManager。"""
        if project_dir:
            path = Path(project_dir)
            return path if path.is_dir() else None
        if project_id:
            try:
                from clipwright.services.project_manager import ProjectManager
                path = ProjectManager().project_dir(project_id)
            except Exception as exc:
                logger.warning("project_memory 解析项目目录失败: %s", exc)
                return None
            return path if path.is_dir() else None
        return None

    @staticmethod
    def _normalize_entries(entries: Any) -> list[tuple[str, str]]:
        """归一化 entries 成 [(段名, 文本)]。"""
        if isinstance(entries, dict):
            out: list[tuple[str, str]] = []
            for section, texts in entries.items():
                if isinstance(texts, str):
                    out.append((str(section), texts))
                else:
                    out.extend((str(section), str(t)) for t in texts)
            return out
        if isinstance(entries, (list, tuple)):
            out = []
            for item in entries:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append((str(item[0]), str(item[1])))
            return out
        return []
