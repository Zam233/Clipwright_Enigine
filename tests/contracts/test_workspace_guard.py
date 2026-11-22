"""workspace_guard：cwd 永远不能逃出任务工作区。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.services.workspace_guard import (
    GuardedCwd,
    WorkspaceGuardError,
    assert_cwd_in_workspace,
    resolve_workspace_root,
)


def _task(root):
    return SimpleNamespace(
        id="task_guard",
        request=SimpleNamespace(extra={"workspace_root": str(root)}),
    )


def test_default_cwd_is_workspace(tmp_path) -> None:
    out = assert_cwd_in_workspace(_task(tmp_path), "")
    assert isinstance(out, GuardedCwd)
    assert out.cwd == tmp_path.resolve()
    assert out.workspace_root == tmp_path.resolve()


def test_relative_subdir_allowed(tmp_path) -> None:
    sub = tmp_path / "materials"
    sub.mkdir()
    out = assert_cwd_in_workspace(_task(tmp_path), "materials")
    assert out.cwd == sub.resolve()


def test_parent_escape_rejected(tmp_path) -> None:
    outside = tmp_path.parent
    with pytest.raises(WorkspaceGuardError) as exc:
        assert_cwd_in_workspace(_task(tmp_path), str(outside))
    assert exc.value.category == "cwd_outside_workspace"


def test_absolute_outside_rejected(tmp_path) -> None:
    with pytest.raises(WorkspaceGuardError) as exc:
        assert_cwd_in_workspace(_task(tmp_path), str(tmp_path.parent))
    assert exc.value.category == "cwd_outside_workspace"


def test_missing_workspace_rejected() -> None:
    task = SimpleNamespace(id="t", request=SimpleNamespace(extra={}))
    with pytest.raises(WorkspaceGuardError) as exc:
        resolve_workspace_root(task)
    assert exc.value.category == "workspace_required"


def test_symlink_escape_rejected(tmp_path) -> None:
    outside = tmp_path.parent / "outside_guard_test"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link_out"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not available")
    with pytest.raises(WorkspaceGuardError) as exc:
        assert_cwd_in_workspace(_task(tmp_path), "link_out")
    assert exc.value.category == "cwd_outside_workspace"
