"""M3：通用 Job Broker（runtime 批准、argv 校验、超时/取消、归属、幂等、恢复）。"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from clipwright.services.plugin_jobs import (
    JobError,
    PluginJobManager,
    RuntimeRegistry,
    RuntimeSpec,
    TERMINAL,
)
from clipwright.services.plugin_workspace import PluginScope, WorkspaceScopeError


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    registry = RuntimeRegistry()
    registry.register(RuntimeSpec(
        runtime_id="py", executable=sys.executable, approved=True,
    ))
    registry.register(RuntimeSpec(
        runtime_id="unapproved", executable=sys.executable, approved=False,
    ))
    return PluginJobManager(registry, max_concurrency=2)


@pytest.fixture
def scope(tmp_path):
    return PluginScope("demo", "proj-1", task_id="t1")


async def _wait(manager, scope, job_id, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        info = manager.get(scope, job_id)
        if info["status"] in TERMINAL:
            return info
        await asyncio.sleep(0.05)
    raise AssertionError(f"job 未结束: {manager.get(scope, job_id)}")


class TestSubmit:
    @pytest.mark.asyncio
    async def test_success_runs_in_workspace(self, manager, scope):
        job = await manager.submit(
            scope, runtime_id="py",
            argv=["-c", "open('out.txt','w').write('hi'); print('done')"],
        )
        info = await _wait(manager, scope, job.job_id)
        assert info["status"] == "succeeded"
        assert "done" in info["stdout_tail"]

    @pytest.mark.asyncio
    async def test_unapproved_runtime_rejected(self, manager, scope):
        with pytest.raises(JobError) as err:
            await manager.submit(scope, runtime_id="unapproved", argv=["-c", "print(1)"])
        assert err.value.code == "UNSUPPORTED_RUNTIME"

    @pytest.mark.asyncio
    async def test_empty_argv_rejected(self, manager, scope):
        with pytest.raises(JobError) as err:
            await manager.submit(scope, runtime_id="py", argv=[])
        assert err.value.code == "BAD_ARGV"

    @pytest.mark.asyncio
    async def test_cwd_traversal_rejected(self, manager, scope):
        with pytest.raises(WorkspaceScopeError):
            await manager.submit(
                scope, runtime_id="py", argv=["-c", "print(1)"], cwd="../..",
            )

    @pytest.mark.asyncio
    async def test_env_secrets_not_inherited(self, manager, scope, monkeypatch):
        monkeypatch.setenv("CLIPWRIGHT_API_TOKEN", "super-secret")
        job = await manager.submit(
            scope, runtime_id="py",
            argv=["-c", "import os;print(os.environ.get('CLIPWRIGHT_API_TOKEN','MISSING'))"],
        )
        info = await _wait(manager, scope, job.job_id)
        assert "MISSING" in info["stdout_tail"]
        assert "super-secret" not in info["stdout_tail"]

    @pytest.mark.asyncio
    async def test_idempotency_key_returns_same_job(self, manager, scope):
        first = await manager.submit(
            scope, runtime_id="py", argv=["-c", "print(1)"], idempotency_key="k1",
        )
        second = await manager.submit(
            scope, runtime_id="py", argv=["-c", "print(1)"], idempotency_key="k1",
        )
        assert first.job_id == second.job_id

    async def test_idempotency_key_conflicts_on_different_args(self, manager, scope):
        """B3：同 key 不同参数不能再静默返回旧 JobRecord。"""
        await manager.submit(
            scope, runtime_id="py", argv=["-c", "print(1)"], idempotency_key="k2",
        )
        with pytest.raises(JobError) as err:
            await manager.submit(
                scope, runtime_id="py", argv=["-c", "print(2)"], idempotency_key="k2",
            )
        assert err.value.code == "IDEMPOTENCY_CONFLICT"

    async def test_idempotency_key_is_scoped_per_task(self, manager, scope):
        """不同 task 用同一 key 不得复用彼此的结果。"""
        from clipwright.services.plugin_workspace import PluginScope

        other = PluginScope(
            plugin_id=scope.plugin_id, project_id=scope.project_id, task_id="other-task",
        )
        first = await manager.submit(
            scope, runtime_id="py", argv=["-c", "print(1)"], idempotency_key="shared",
        )
        second = await manager.submit(
            other, runtime_id="py", argv=["-c", "print(1)"], idempotency_key="shared",
        )
        assert first.job_id != second.job_id


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_timeout_marks_timed_out(self, manager, scope):
        job = await manager.submit(
            scope, runtime_id="py",
            argv=["-c", "import time;time.sleep(5)"], timeout_ms=200,
        )
        info = await _wait(manager, scope, job.job_id, timeout=10)
        assert info["status"] == "timed_out"

    @pytest.mark.asyncio
    async def test_cancel(self, manager, scope):
        job = await manager.submit(
            scope, runtime_id="py", argv=["-c", "import time;time.sleep(5)"],
        )
        await asyncio.sleep(0.2)
        info = await manager.cancel(scope, job.job_id)
        assert info["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_owner_mismatch_denied(self, manager, scope):
        job = await manager.submit(scope, runtime_id="py", argv=["-c", "print(1)"])
        other = PluginScope("demo", "proj-2")
        with pytest.raises(JobError) as err:
            manager.get(other, job.job_id)
        assert err.value.code == "PERMISSION_DENIED"
        await _wait(manager, scope, job.job_id)

    @pytest.mark.asyncio
    async def test_cancel_for_task(self, manager, scope):
        job = await manager.submit(
            scope, runtime_id="py", argv=["-c", "import time;time.sleep(5)"],
        )
        await asyncio.sleep(0.2)
        assert manager.cancel_for_task("t1") == 1
        assert manager.get(scope, job.job_id)["status"] == "cancelled"
        assert manager.cancel_for_task("t1") == 0

    @pytest.mark.asyncio
    async def test_recover_interrupted(self, manager, scope):
        job = await manager.submit(
            scope, runtime_id="py", argv=["-c", "import time;time.sleep(5)"],
        )
        await asyncio.sleep(0.2)
        assert manager.get(scope, job.job_id)["status"] == "running"
        assert manager.recover_interrupted() >= 1
        assert manager.get(scope, job.job_id)["status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_listener_gets_cancel_event(self, manager, scope):
        events: list[tuple[str, dict]] = []
        manager.register_listener("demo", lambda name, payload: events.append((name, payload)))
        job = await manager.submit(
            scope, runtime_id="py", argv=["-c", "import time;time.sleep(5)"],
        )
        await asyncio.sleep(0.2)
        await manager.cancel(scope, job.job_id)
        assert events and events[0][0] == "job.cancelled"
        assert events[0][1]["job_id"] == job.job_id

    @pytest.mark.asyncio
    async def test_listener_gets_task_cancel_event(self, manager, scope):
        events: list[tuple[str, dict]] = []
        manager.register_listener("demo", lambda name, payload: events.append((name, payload)))
        await manager.submit(
            scope, runtime_id="py", argv=["-c", "import time;time.sleep(5)"],
        )
        await asyncio.sleep(0.2)
        manager.cancel_for_task("t1")
        assert any(name == "task.cancelled" for name, _ in events)

    @pytest.mark.asyncio
    async def test_listener_gets_timeout_event(self, manager, scope):
        events: list[tuple[str, dict]] = []
        manager.register_listener("demo", lambda name, payload: events.append((name, payload)))
        job = await manager.submit(
            scope, runtime_id="py",
            argv=["-c", "import time;time.sleep(5)"], timeout_ms=200,
        )
        await _wait(manager, scope, job.job_id, timeout=10)
        assert any(name == "job.timed_out" for name, _ in events)
