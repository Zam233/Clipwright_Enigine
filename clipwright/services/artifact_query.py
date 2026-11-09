"""通用 artifact 查询（M6）—— 所有视频类型共用，不绑定插件领域。

数据来源：`<project-root>/.engine/artifacts/<plugin_id>.json`
（由 `PluginArtifactRegistry` 登记；engine 计算 size/sha256/mime）。

能力：
- 按 project/task/plugin/status/role 过滤，`created_at desc` 排序，cursor 分页；
- 按 `(plugin_id, artifact_id)` 取原始文件路径，校验仍在插件命名空间内。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.services.workspace import Workspace

#: 单页上限
MAX_LIMIT = 100
DEFAULT_LIMIT = 50


class ArtifactNotFound(LookupError):
    pass


@dataclass
class ArtifactPage:
    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str = ""
    total_estimate: int | None = None


def _registry_dir(project_id: str) -> Path:
    return Workspace(project_id).dirs().root / ".engine" / "artifacts"


def _load_entries(project_id: str, plugin_id: str = "") -> list[dict[str, Any]]:
    base = _registry_dir(project_id)
    if not base.is_dir():
        return []
    files = [base / f"{plugin_id}.json"] if plugin_id else sorted(base.glob("*.json"))
    items: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("artifact 登记表读取失败 %s: %s", path, e)
            continue
        for entry in data.get("artifacts") or []:
            if isinstance(entry, dict):
                items.append(entry)
    return items


def _cursor_of(entry: dict[str, Any]) -> str:
    return f"{float(entry.get('created_at') or 0):.6f}:{entry.get('artifact_id', '')}"


def list_artifacts(
    project_id: str,
    *,
    plugin_id: str = "",
    task_id: str = "",
    status: str = "",
    role: str = "",
    cursor: str = "",
    limit: int = DEFAULT_LIMIT,
) -> ArtifactPage:
    """分页查询 artifact（cursor 为上一页最后一条的 `_cursor`）。"""
    if not project_id:
        raise ValueError("project_id 不能为空")
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    items = _load_entries(project_id, plugin_id)
    if task_id:
        items = [i for i in items if str(i.get("task_id") or "") == task_id]
    if plugin_id:
        items = [i for i in items if str(i.get("plugin_id") or "") == plugin_id]
    if status:
        items = [i for i in items if str(i.get("status") or "") == status]
    if role:
        items = [i for i in items if str(i.get("role") or "") == role]
    items.sort(key=_cursor_of, reverse=True)
    total = len(items)
    if cursor:
        items = [i for i in items if _cursor_of(i) < cursor]
    page = items[:limit]
    for item in page:
        item["_cursor"] = _cursor_of(item)
    next_cursor = page[-1]["_cursor"] if len(items) > limit and page else ""
    return ArtifactPage(items=page, next_cursor=next_cursor, total_estimate=total)


def artifact_path(project_id: str, plugin_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
    """取 artifact 文件；校验仍在 `<project>/plugins/<plugin>/` 内。"""
    for entry in _load_entries(project_id, plugin_id):
        if str(entry.get("artifact_id") or "") != artifact_id:
            continue
        if str(entry.get("plugin_id") or "") != plugin_id:
            continue
        root = Workspace(project_id).dirs().root / "plugins" / plugin_id
        target = (root / str(entry.get("path") or "")).resolve()
        root_real = root.resolve()
        if not target.is_relative_to(root_real):
            raise PermissionError(f"artifact 路径越界: {entry.get('path')!r}")
        if not target.is_file():
            raise ArtifactNotFound(f"artifact 文件不存在: {artifact_id}")
        return target, entry
    raise ArtifactNotFound(f"artifact 不存在: {artifact_id}")


__all__ = [
    "ArtifactNotFound", "ArtifactPage", "DEFAULT_LIMIT", "MAX_LIMIT",
    "artifact_path", "list_artifacts",
]
