"""任务工作区 cwd 守卫（PRD kernel-exec-approval §7）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspaceGuardError(ValueError):
    """cwd/工作区不合法；调用方必须拒绝执行。"""

    def __init__(self, category: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.category = category
        self.detail = dict(detail)


@dataclass(frozen=True)
class GuardedCwd:
    workspace_root: Path
    cwd: Path

    def as_posix(self) -> str:
        return str(self.cwd)


def _task_extra(task: Any) -> dict[str, Any]:
    request = getattr(task, "request", None)
    extra = getattr(request, "extra", None) if request is not None else None
    if isinstance(extra, dict):
        return extra
    extra = getattr(task, "extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def resolve_workspace_root(task: Any) -> Path:
    """解析任务工作区根；找不到就抛 workspace_required。"""
    extra = _task_extra(task)
    raw = str(extra.get("workspace_root") or "").strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root)
        root = root.resolve()
        if not root.is_dir():
            raise WorkspaceGuardError(
                "workspace_required", f"工作区不存在或不是目录: {root}",
                workspace_root=str(root),
            )
        return root

    task_id = str(getattr(task, "id", "") or "")
    if task_id:
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(task_id)
            if ws.exists:
                root = ws.dirs().root.expanduser().resolve()
                if root.is_dir():
                    return root
        except Exception as exc:  # noqa: BLE001
            raise WorkspaceGuardError(
                "workspace_required", f"无法解析任务工作区: {exc}",
                task_id=task_id,
            ) from exc
    raise WorkspaceGuardError(
        "workspace_required", "任务未绑定工作区，不能执行命令",
        task_id=task_id,
    )


def assert_cwd_in_workspace(task: Any, requested: str = "") -> GuardedCwd:
    """把 cwd 归一化并限制在工作区；越界抛错。"""
    root = resolve_workspace_root(task)
    raw = str(requested or "").strip()
    candidate = Path(raw).expanduser() if raw else root
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise WorkspaceGuardError(
            "cwd_not_directory", f"cwd 无法解析: {candidate}", cwd=str(candidate),
        ) from exc

    if not resolved.is_dir():
        raise WorkspaceGuardError(
            "cwd_not_directory", f"cwd 不存在或不是目录: {resolved}",
            cwd=str(resolved), workspace_root=str(root),
        )
    # 盘符根 / UNC 根 / 用户主目录本身都不允许
    if resolved.parent == resolved:
        raise WorkspaceGuardError(
            "cwd_outside_workspace", f"cwd 不能是文件系统根: {resolved}",
            cwd=str(resolved), workspace_root=str(root),
        )

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceGuardError(
            "cwd_outside_workspace", "cwd 必须位于任务工作区内",
            cwd=str(resolved), workspace_root=str(root),
        ) from exc

    return GuardedCwd(workspace_root=root, cwd=resolved)


def resolve_script_path(task: Any, name: str = "script.py") -> Path:
    """run_python 脚本只能落在工作区 .supervisor/scripts。"""
    guarded = assert_cwd_in_workspace(task, "")
    base = guarded.workspace_root / ".supervisor" / "scripts"
    base.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(name or "script.py"))
    return base / safe
