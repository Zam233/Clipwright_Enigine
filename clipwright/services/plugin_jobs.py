"""插件通用 Job Broker（M3）。

engine 只认识**不透明** runtime 与通用 JobSpec：
- runtime 必须由管理员登记（executable + 平台 + digest），插件不能自带可执行路径；
- argv 以列表传入、`shell=False`，不做字符串拼接；
- cwd / 输入 / 输出都必须是插件 workspace 内的相对路径；
- 环境变量走白名单，默认不继承 API/model 凭据；
- 超时、取消、进程树回收、状态机与重启恢复由宿主负责。

本模块不含任何视频/动效领域语义；Remotion/Node/Python 都只是插件选择的 runtime。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.services.plugin_workspace import PluginScope, PluginWorkspace

#: 单次 job 墙钟上限（毫秒）；插件可更小，不可更大
MAX_TIMEOUT_MS = 60 * 60 * 1000
#: argv 数量/单项长度上限
MAX_ARGS = 64
MAX_ARG_CHARS = 4096
#: 环境变量白名单（不继承 API token、模型密钥等）
ENV_ALLOWLIST = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL", "HOME",
)

#: 终态集合（不可回退）
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


class JobError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimeSpec:
    """管理员登记的不透明运行时。"""

    runtime_id: str
    executable: str
    platform: str = sys.platform
    version: str = ""
    digest: str = ""
    approved: bool = False


@dataclass
class JobRecord:
    job_id: str
    plugin_id: str
    task_id: str
    project_id: str
    runtime_id: str
    argv: list[str]
    cwd: str
    outputs: list[str] = field(default_factory=list)
    status: str = "queued"
    exit_code: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "plugin_id": self.plugin_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "runtime_id": self.runtime_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "outputs": list(self.outputs),
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


class RuntimeRegistry:
    """runtime_id → RuntimeSpec；只接受已批准项用于执行。"""

    def __init__(self) -> None:
        self._items: dict[str, RuntimeSpec] = {}

    def register(self, spec: RuntimeSpec) -> RuntimeSpec:
        if not spec.runtime_id.strip():
            raise JobError("BAD_RUNTIME", "runtime_id 不能为空")
        self._items[spec.runtime_id] = spec
        return spec

    def get(self, runtime_id: str) -> RuntimeSpec:
        spec = self._items.get(str(runtime_id or ""))
        if spec is None:
            raise JobError("UNKNOWN_RUNTIME", f"未登记的 runtime: {runtime_id}")
        return spec

    def require_approved(self, runtime_id: str) -> RuntimeSpec:
        spec = self.get(runtime_id)
        if not spec.approved:
            raise JobError("UNSUPPORTED_RUNTIME", f"runtime 未批准: {runtime_id}")
        if not Path(spec.executable).is_file():
            raise JobError("UNSUPPORTED_RUNTIME", f"runtime 可执行文件不存在: {spec.executable}")
        # B3：声明了 digest 就必须校验，防止批准的运行时文件被替换。
        if spec.digest:
            actual = _file_sha256(spec.executable)
            if actual.lower() != spec.digest.strip().lower():
                raise JobError(
                    "UNSUPPORTED_RUNTIME",
                    f"runtime 文件摘要不匹配: 期望 {spec.digest[:16]}… 实际 {actual[:16]}…",
                )
        return spec

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "runtime_id": s.runtime_id,
                "platform": s.platform,
                "version": s.version,
                "approved": s.approved,
            }
            for s in self._items.values()
        ]


def _env_for_child() -> dict[str, str]:
    return {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}


def _file_sha256(path: str | Path, *, chunk: int = 1024 * 1024) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _idem_scope_key(scope: PluginScope, idempotency_key: str) -> str:
    """幂等键作用域：插件 + 项目 + 任务 + 用户 key（B3）。"""
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    return "\x1f".join((scope.plugin_id, scope.project_id, scope.task_id or "", key))


class PluginJobManager:
    """按插件/project 管理 job 生命周期。线程/事件循环模型与 PluginHost 一致。"""

    def __init__(self, registry: RuntimeRegistry, *, max_concurrency: int = 2) -> None:
        self.registry = registry
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._idem: dict[str, str] = {}
        self._sem = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._locks: dict[str, asyncio.Lock] = {}
        #: 首次 submit 时记录宿主循环，供 task 取消（同步调用点）调度进程终止
        self._loop: asyncio.AbstractEventLoop | None = None
        #: 事件监听（M4）：plugin_id → fn(name, payload)
        self._listeners: dict[str, Any] = {}

    def register_listener(self, plugin_id: str, fn: Any) -> None:
        """注册插件事件监听（通常是 host.notify）。"""
        if plugin_id and callable(fn):
            self._listeners[str(plugin_id)] = fn

    def _notify(self, plugin_id: str, name: str, payload: dict[str, Any]) -> None:
        fn = self._listeners.get(str(plugin_id))
        if fn is None:
            return
        try:
            fn(str(name), dict(payload))
        except Exception as e:  # noqa: BLE001 — 通知失败不影响 job 状态
            logger.debug("job 事件通知失败 %s/%s: %s", plugin_id, name, e)

    # ── 提交 ──────────────────────────────────────────
    async def submit(
        self,
        scope: PluginScope,
        *,
        runtime_id: str,
        argv: list[str],
        cwd: str = "",
        outputs: list[str] | None = None,
        timeout_ms: int = 600_000,
        idempotency_key: str = "",
    ) -> JobRecord:
        spec = self.registry.require_approved(runtime_id)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        args = self._validate_argv(spec, argv)
        workspace = PluginWorkspace(scope)
        # B3：默认 cwd 是插件根（宿主负责建根）；其它 cwd 必须是已存在目录，
        # 不能因为解析路径而在工作区里凭空造子目录。
        if (cwd or ".") in ("", "."):
            work_dir = workspace._ensure_root()
        else:
            work_dir = workspace.resolve(cwd, must_exist_parent=False)
        if not work_dir.is_dir():
            raise JobError("BAD_CWD", f"cwd 不是已存在目录: {cwd!r}")
        out_rel = [str(p) for p in (outputs or [])]
        for rel in out_rel:
            workspace.resolve(rel)  # 只校验范围，不要求已存在
        # B3：幂等键按 (plugin, project, task, key) 分区；复用前核对参数，
        # 不同作用域同 key 绝不返回他人 JobRecord。
        idem_scope = _idem_scope_key(scope, idempotency_key)
        if idem_scope:
            existing = self._idem.get(idem_scope)
            if existing and existing in self._jobs:
                previous = self._jobs[existing]
                if (
                    previous.runtime_id == runtime_id
                    and previous.argv == args
                    and previous.cwd == (cwd or ".")
                    and previous.outputs == out_rel
                ):
                    return previous
                raise JobError(
                    "IDEMPOTENCY_CONFLICT",
                    "相同幂等键用于不同的 job 参数；请使用新的幂等键",
                )

        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:16]}",
            plugin_id=scope.plugin_id,
            task_id=scope.task_id,
            project_id=scope.project_id,
            runtime_id=runtime_id,
            argv=args,
            cwd=cwd or ".",
            outputs=out_rel,
            idempotency_key=idempotency_key,
        )
        self._jobs[job.job_id] = job
        if idem_scope:
            self._idem[idem_scope] = job.job_id
        task = asyncio.create_task(self._run(job, spec, workspace, min(int(timeout_ms), MAX_TIMEOUT_MS)))
        self._tasks[job.job_id] = task
        return job

    # ── 查询/取消 ─────────────────────────────────────
    def get(self, scope: PluginScope, job_id: str) -> dict[str, Any]:
        job = self._owned(scope, job_id)
        return job.to_dict()

    def list(self, scope: PluginScope, *, status: str = "") -> list[dict[str, Any]]:
        items = [
            j for j in self._jobs.values()
            if j.plugin_id == scope.plugin_id and j.project_id == scope.project_id
        ]
        if status:
            items = [j for j in items if j.status == status]
        return [j.to_dict() for j in items]

    async def cancel(self, scope: PluginScope, job_id: str) -> dict[str, Any]:
        job = self._owned(scope, job_id)
        if job.status in TERMINAL:
            return job.to_dict()
        job.status = "cancelled"
        job.finished_at = time.time()
        proc = self._procs.get(job.job_id)
        await self._kill_tree(proc)
        task = self._tasks.get(job.job_id)
        if task and not task.done():
            task.cancel()
        self._notify(job.plugin_id, "job.cancelled",
                     {"job_id": job.job_id, "reason": "user_cancel"})
        return job.to_dict()

    def cancel_for_task(self, task_id: str) -> int:
        """同步入口（供 kernel.cancel 调用）：取消该任务关联的全部在途 job。

        进程终止是 async 的，这里投递到宿主循环，不阻塞取消流程；状态先落
        cancelled，保证取消结果不会被后续完成事件覆盖成 succeeded。
        """
        count = 0
        for job in self._jobs.values():
            if job.task_id != str(task_id or "") or job.status in TERMINAL:
                continue
            job.status = "cancelled"
            job.finished_at = time.time()
            job.error = "任务被取消"
            self._notify(job.plugin_id, "task.cancelled",
                         {"job_id": job.job_id, "task_id": job.task_id})
            proc = self._procs.get(job.job_id)
            task = self._tasks.get(job.job_id)
            loop = self._loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(self._kill_tree(proc), loop)
                if task is not None and not task.done():
                    loop.call_soon_threadsafe(task.cancel)
            count += 1
        return count

    # ── 内部 ──────────────────────────────────────────
    def _owned(self, scope: PluginScope, job_id: str) -> JobRecord:
        job = self._jobs.get(str(job_id or ""))
        if job is None:
            raise JobError("NOT_FOUND", f"job 不存在: {job_id}")
        if (job.plugin_id, job.project_id) != (scope.plugin_id, scope.project_id):
            raise JobError("PERMISSION_DENIED", "job 归属不匹配")
        # B3：同一插件/项目内仍按任务隔离；空 task 的旧记录保持兼容可读。
        if scope.task_id and job.task_id and job.task_id != scope.task_id:
            raise JobError("PERMISSION_DENIED", "job 不属于当前任务")
        return job

    def _validate_argv(self, spec: RuntimeSpec, argv: list[str]) -> list[str]:
        if not isinstance(argv, list) or not argv:
            raise JobError("BAD_ARGV", "argv 必须是非空列表")
        if len(argv) > MAX_ARGS:
            raise JobError("BAD_ARGV", f"argv 超过 {MAX_ARGS} 项")
        for item in argv:
            if not isinstance(item, str):
                raise JobError("BAD_ARGV", "argv 每项必须是字符串")
            if len(item) > MAX_ARG_CHARS:
                raise JobError("BAD_ARGV", "argv 单项过长")
            if "\x00" in item or "\n" in item or "\r" in item:
                raise JobError("BAD_ARGV", "argv 含控制字符")
        return list(argv)

    async def _run(self, job: JobRecord, spec: RuntimeSpec,
                   workspace: PluginWorkspace, timeout_ms: int) -> None:
        async with self._sem:
            if job.status in TERMINAL:
                return
            job.status = "running"
            job.started_at = time.time()
            cwd = workspace.resolve(job.cwd)
            env = _env_for_child()
            try:
                proc = await asyncio.create_subprocess_exec(
                    spec.executable, *job.argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # B3：独立 session/进程组，取消时只杀 job 子树，绝不波及宿主进程组。
                    start_new_session=(sys.platform != "win32"),
                )
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error = f"启动失败: {e}"
                job.finished_at = time.time()
                return
            self._procs[job.job_id] = proc
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_ms / 1000.0,
                )
                job.exit_code = proc.returncode
                job.stdout_tail = _tail(out)
                job.stderr_tail = _tail(err)
                if job.status == "cancelled":
                    return
                if proc.returncode == 0:
                    job.status = "succeeded"
                else:
                    job.status = "failed"
                    job.error = f"退出码 {proc.returncode}"
            except asyncio.TimeoutError:
                job.status = "timed_out"
                job.error = f"超时（{timeout_ms}ms）"
                self._notify(job.plugin_id, "job.timed_out",
                             {"job_id": job.job_id, "timeout_ms": timeout_ms})
                await self._kill_tree(proc)
            except asyncio.CancelledError:
                job.status = "cancelled"
                await self._kill_tree(proc)
                raise
            finally:
                job.finished_at = time.time()
                self._procs.pop(job.job_id, None)

    async def _kill_tree(self, proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, check=False, timeout=10,
                )
            else:
                # B3：独立 session 里 PID 即 PGID；仍用 getpgid 核对，避免配置漂移把
                # 信号发给宿主所在进程组。
                pgid = os.getpgid(proc.pid)
                if pgid == os.getpgid(0):
                    logger.warning(
                        "拒绝 killpg: job pid=%s 与宿主同组 %s", proc.pid, pgid,
                    )
                else:
                    os.killpg(pgid, 9)
        except Exception as e:  # noqa: BLE001
            logger.warning("终止 job 进程树失败 pid=%s: %s", getattr(proc, "pid", "?"), e)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:  # noqa: BLE001
            pass

    # ── 重启恢复 ──────────────────────────────────────
    def recover_interrupted(self) -> int:
        """宿主启动时调用：running/queued 一律标 interrupted（不伪造成功）。"""
        count = 0
        for job in self._jobs.values():
            if job.status in ("queued", "running"):
                job.status = "interrupted"
                job.error = "宿主重启，执行中断"
                job.finished_at = time.time()
                count += 1
        return count


def _tail(data: bytes | None, limit: int = 4000) -> str:
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace")
    return text[-limit:]


def dump_jobs(jobs: list[JobRecord]) -> str:
    return json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2)
