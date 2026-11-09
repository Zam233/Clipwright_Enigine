"""插件 RPC 的宿主侧通用 handler（M2）。

只注册**通用**方法：workspace.* / artifact.*。不带任何动效或视频领域参数；
handler 的入参就是路径、内容、role 等通用字段。

每个请求都必须带 scope（plugin_id/project_id/task_id）；宿主校验
scope.plugin_id 与当前 host 一致，并按 scope 构造受限视图，插件无法跨项目读写。
"""
from __future__ import annotations

import inspect
from typing import Any

from clipwright.plugins.runtime.protocol import (
    ERR_PERMISSION_DENIED,
    RpcError,
)
from clipwright.services.plugin_jobs import (
    JobError,
    PluginJobManager,
    RuntimeRegistry,
    RuntimeSpec,
)
from clipwright.services.plugin_materials import (
    MAX_READ_BYTES,
    MaterialError,
    list_materials,
    read_material,
)
from clipwright.services.plugin_workspace import (
    PluginArtifactRegistry,
    PluginScope,
    PluginWorkspace,
    WorkspaceRevisionConflict,
    WorkspaceScopeError,
)

ERR_NOT_FOUND = "NOT_FOUND"
ERR_REVISION_CONFLICT = "REVISION_CONFLICT"

#: 进程内共享的 runtime 登记表 / job 管理器（管理员登记 runtime 后所有插件共用）
_RUNTIMES = RuntimeRegistry()
_JOB_MANAGER: PluginJobManager | None = None


def runtime_registry() -> RuntimeRegistry:
    return _RUNTIMES


def register_runtime(spec: RuntimeSpec) -> RuntimeSpec:
    """管理员/安装流程登记一个 runtime（生产环境需接审批；测试可直接调用）。"""
    return _RUNTIMES.register(spec)


def job_manager() -> PluginJobManager:
    global _JOB_MANAGER
    if _JOB_MANAGER is None:
        _JOB_MANAGER = PluginJobManager(_RUNTIMES)
    return _JOB_MANAGER


def _job_wrap(plugin_id: str, fn: Any) -> Any:
    """job handler：校验 scope、解包 JobError、支持 async。"""

    async def _handler(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        try:
            parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
            result = fn(parsed, dict(params or {}))
            if inspect.isawaitable(result):
                result = await result
            return result
        except (WorkspaceScopeError, MaterialError) as e:
            raise RpcError(ERR_PERMISSION_DENIED, str(e)) from e
        except JobError as e:
            raise RpcError(e.code, e.message) from e

    return _handler


def _wrap(fn: Any) -> Any:
    """把服务异常翻译成稳定 RPC 错误码（不把 Python 异常类型漏给插件）。"""

    def _handler(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        try:
            return fn(dict(params or {}), dict(scope or {}))
        except (WorkspaceScopeError, MaterialError) as e:
            raise RpcError(ERR_PERMISSION_DENIED, str(e)) from e
        except WorkspaceRevisionConflict as e:
            raise RpcError(ERR_REVISION_CONFLICT, str(e)) from e
        except FileNotFoundError as e:
            raise RpcError(ERR_NOT_FOUND, str(e)) from e
        except FileExistsError as e:
            raise RpcError("ALREADY_EXISTS", str(e)) from e
        except ValueError as e:
            raise RpcError("BAD_REQUEST", str(e)) from e

    return _handler


def install_default_handlers(host: Any) -> None:
    """给一个 PluginHost 注册 workspace/artifact 通用方法。"""
    plugin_id = str(getattr(host, "plugin_id", "") or "")

    def _workspace(scope: dict[str, Any]) -> PluginWorkspace:
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        return PluginWorkspace(parsed)

    def _artifacts(scope: dict[str, Any]) -> PluginArtifactRegistry:
        return PluginArtifactRegistry(_workspace(scope))

    def _path(params: dict[str, Any]) -> str:
        value = str(params.get("path") or "").strip()
        if not value:
            raise RpcError("BAD_REQUEST", "path 不能为空")
        return value

    @_wrap
    def workspace_read(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).read(_path(params))

    @_wrap
    def workspace_write(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).write_atomic(
            _path(params),
            params.get("data", ""),
            expected_revision=params.get("expected_revision"),
        )

    @_wrap
    def workspace_list(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).list(str(params.get("path") or "."))

    @_wrap
    def workspace_stat(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).stat(_path(params))

    @_wrap
    def workspace_hash(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).hash(_path(params))

    @_wrap
    def workspace_mkdir(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).mkdir(_path(params))

    @_wrap
    def workspace_delete(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _workspace(scope).delete(_path(params))

    @_wrap
    def workspace_rename(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        src = str(params.get("src") or "").strip()
        dst = str(params.get("dst") or "").strip()
        if not src or not dst:
            raise RpcError("BAD_REQUEST", "rename 需要 src 与 dst")
        return _workspace(scope).rename(src, dst)

    @_wrap
    def artifact_register(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        return _artifacts(scope).register(
            _path(params),
            role=str(params.get("role") or "render"),
            mime=str(params.get("mime") or ""),
            job_id=str(params.get("job_id") or ""),
            retention=str(params.get("retention") or "task"),
            status=str(params.get("status") or "ready"),
        )

    @_wrap
    def artifact_list(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        mode = str(params.get("scope") or "own").strip().lower()
        if mode in ("", "own"):
            return _artifacts(scope).list(
                status=str(params.get("status") or ""),
                role=str(params.get("role") or ""),
            )
        if mode == "task":
            # 只读同任务产物（其他阶段/插件的产出），供插件复用
            from clipwright.services.artifact_query import list_artifacts

            page = list_artifacts(
                parsed.project_id, task_id=parsed.task_id,
                limit=int(params.get("limit") or 50),
            )
            return [
                {k: v for k, v in item.items() if k != "_cursor"}
                for item in page.items
            ]
        raise RpcError(
            ERR_PERMISSION_DENIED,
            f"artifact scope={mode!r} 未授权（可用: own / task）",
        )

    @_wrap
    def materials_list(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        return list_materials(
            parsed.project_id, limit=int(params.get("limit") or 32),
        )

    @_wrap
    def materials_read(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        ref = str(params.get("ref") or params.get("path") or "")
        return read_material(
            parsed.project_id, ref,
            max_bytes=int(params.get("max_bytes") or MAX_READ_BYTES),
        )

    @_wrap
    def artifact_get(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        artifact_id = str(params.get("artifact_id") or "").strip()
        if not artifact_id:
            raise RpcError("BAD_REQUEST", "artifact_id 不能为空")
        return _artifacts(scope).get(artifact_id)

    # ── 通用 job（M3）──────────────────────────────────
    async def job_submit(scope: PluginScope, params: dict[str, Any]) -> Any:
        record = await job_manager().submit(
            scope,
            runtime_id=str(params.get("runtime_id") or ""),
            argv=list(params.get("argv") or []),
            cwd=str(params.get("cwd") or ""),
            outputs=[str(p) for p in (params.get("outputs") or [])],
            timeout_ms=int(params.get("timeout_ms") or 600_000),
            idempotency_key=str(params.get("idempotency_key") or ""),
        )
        return record.to_dict()

    def job_status(scope: PluginScope, params: dict[str, Any]) -> Any:
        return job_manager().get(scope, str(params.get("job_id") or ""))

    def job_list(scope: PluginScope, params: dict[str, Any]) -> Any:
        return job_manager().list(scope, status=str(params.get("status") or ""))

    async def job_cancel(scope: PluginScope, params: dict[str, Any]) -> Any:
        return await job_manager().cancel(scope, str(params.get("job_id") or ""))

    host.register_handler("workspace.read", workspace_read)
    host.register_handler("workspace.write", workspace_write)
    host.register_handler("workspace.list", workspace_list)
    host.register_handler("workspace.stat", workspace_stat)
    host.register_handler("workspace.hash", workspace_hash)
    host.register_handler("workspace.mkdir", workspace_mkdir)
    host.register_handler("workspace.delete", workspace_delete)
    host.register_handler("workspace.rename", workspace_rename)
    host.register_handler("artifact.register", artifact_register)
    host.register_handler("artifact.list", artifact_list)
    host.register_handler("artifact.get", artifact_get)
    host.register_handler("materials.list", materials_list)
    host.register_handler("materials.read", materials_read)
    host.register_handler("job.submit", _job_wrap(plugin_id, job_submit))
    host.register_handler("job.status", _job_wrap(plugin_id, job_status))
    host.register_handler("job.list", _job_wrap(plugin_id, job_list))
    host.register_handler("job.cancel", _job_wrap(plugin_id, job_cancel))


def plugin_hosts_for(timeline: Any) -> dict[str, Any]:
    """收集时间线里插件 clip 对应的 worker host（plugin_id → host）。"""
    from clipwright.services.clip_kind_registry import ClipKindRegistry

    plugin_ids: set[str] = set()
    for track in getattr(timeline, "tracks", []) or []:
        for clip in getattr(track, "clips", []) or []:
            definition = ClipKindRegistry.get(str(getattr(clip, "kind", "")))
            if definition is not None and not definition.builtin:
                plugin_ids.add(definition.owner_plugin)
    if not plugin_ids:
        return {}
    try:
        from clipwright.main import _plugin_loader as loader  # 进程级 loader
    except Exception:  # noqa: BLE001 — 早期启动/单测没有 loader
        return {}
    hosts = dict(getattr(loader, "_hosts", {}) or {})
    return {pid: hosts[pid] for pid in plugin_ids if pid in hosts}


def plugin_render_kwargs(
    timeline: Any, *, project_id: str = "", task_id: str = "",
) -> dict[str, Any]:
    """给 `RenderService.render` 准备通用插件回调参数；没有插件片段时返回空。"""
    hosts = plugin_hosts_for(timeline)
    if not hosts:
        return {}

    def _resolve(plugin_id: str) -> Any:
        host = hosts.get(str(plugin_id))
        if host is None:
            raise RpcError("UNKNOWN_PLUGIN_HOST", f"插件未运行: {plugin_id}")
        return host

    return {
        "plugin_host": _resolve,
        "plugin_scope": {"project_id": project_id, "task_id": task_id},
    }


__all__ = [
    "install_default_handlers", "job_manager", "plugin_hosts_for",
    "plugin_render_kwargs", "register_runtime", "runtime_registry",
    "ERR_NOT_FOUND", "ERR_REVISION_CONFLICT",
]
