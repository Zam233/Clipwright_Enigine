"""产物读取工具（PRD §17.5）：`list_artifacts` / `read_artifact`。

产物由 `stage_subagent.persist_artifacts` 写成 `<ws>/artifacts/art_xxxx.json`（不可变），
阶段结果里只留引用（`{artifact, path, sha256, bytes, preview}`）。主 agent 先 list 再按需 read，
避免把几 MB 的时间线塞进上下文。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.tool.base import BaseTool, ToolExecResult, ToolStatus

#: 单次读取上限（字节）；超出提示用 json_path 精确取值
READ_ARTIFACT_MAX_BYTES = 200_000


def _resolve_root(project_id: str = "", workspace_root: str = "") -> Path | None:
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


def _preview_of(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: str(payload[k])[:60] for k in list(payload)[:6]}
    if isinstance(payload, list):
        return f"{len(payload)} 项"
    return str(payload)[:200]


class ListArtifactsTool(BaseTool):
    """列出工作区 artifacts/ 下的产物（引用 + preview）。"""

    name = "list_artifacts"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "列出工作区 artifacts/ 下的结构化产物（artifact id / 大小 / preview）。"
        "只读；要看正文请用 read_artifact。"
    )

    async def execute(
        self,
        project_id: str = "",
        workspace_root: str = "",
        limit: int = 20,
        **kwargs: Any,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        if root is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="未找到工作区（需要 project_id 或 workspace_root）")
        base = root / "artifacts"
        items: list[dict[str, Any]] = []
        if base.is_dir():
            for path in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime,
                               reverse=True)[: max(1, int(limit or 20))]:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                preview: Any = ""
                try:
                    preview = _preview_of(json.loads(path.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001 — 坏文件也要列出来
                    preview = "（无法解析）"
                items.append({
                    "artifact": path.stem,
                    "path": str(path),
                    "bytes": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "preview": preview,
                })
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"workspace_root": str(root), "count": len(items), "artifacts": items},
        )


class ReadArtifactTool(BaseTool):
    """读一个产物（默认返回前 200KB；可用 json_path 取子树）。"""

    name = "read_artifact"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "读取工作区产物的 JSON 内容。参数：`artifact`（id，如 art_1a2b3c4d）或 `path`；"
        "可选 `json_path`（如 `tracks.0.clips`）只取子树。单次最多 200KB。"
    )

    async def execute(
        self,
        artifact: str = "",
        path: str = "",
        project_id: str = "",
        workspace_root: str = "",
        json_path: str = "",
        max_bytes: int = READ_ARTIFACT_MAX_BYTES,
        **kwargs: Any,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        target: Path | None = None
        if path:
            target = Path(str(path))
        elif artifact and root is not None:
            candidate = root / "artifacts" / f"{Path(str(artifact)).stem}.json"
            if candidate.is_file():
                target = candidate
        if target is None or not target.is_file():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="找不到产物（给 artifact id 或 path）")

        from clipwright.security import SecurityViolation, assert_allowed_path

        try:
            assert_allowed_path(target)
        except SecurityViolation as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"路径不在允许的目录内: {exc}")

        try:
            raw = target.read_bytes()
        except OSError as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))

        limit = max(1024, int(max_bytes or READ_ARTIFACT_MAX_BYTES))
        truncated = len(raw) > limit
        payload: Any
        try:
            data = json.loads(raw[:limit].decode("utf-8", errors="ignore")) if truncated \
                else json.loads(raw.decode("utf-8"))
            payload = data
        except Exception:  # noqa: BLE001 — JSON 坏了就返回原文片段
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"path": str(target), "bytes": len(raw), "raw": raw[:limit].decode(
                    "utf-8", errors="ignore")},
                output_path=str(target),
            )

        if json_path:
            payload = _walk(payload, json_path)

        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "path": str(target),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "truncated": truncated,
                "json_path": json_path,
                "content": payload,
            },
            output_path=str(target),
        )


def _walk(payload: Any, dotted: str) -> Any:
    """按 `a.0.b` 取值；取不到返回提示字符串（不抛错）。"""
    cur = payload
    for part in str(dotted).split("."):
        if not part:
            continue
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur[part]
            else:
                return f"（在 {part} 处无法继续：{type(cur).__name__}）"
        except (KeyError, IndexError, ValueError, TypeError):
            return f"（json_path 未命中：{dotted}）"
    return cur


__all__ = ["READ_ARTIFACT_MAX_BYTES", "ListArtifactsTool", "ReadArtifactTool"]
