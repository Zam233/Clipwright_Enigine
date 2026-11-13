"""插件 RPC 的宿主侧通用 handler（M2）。

只注册**通用**方法：workspace.* / artifact.*。不带任何动效或视频领域参数；
handler 的入参就是路径、内容、role 等通用字段。

每个请求都必须带 scope（plugin_id/project_id/task_id）；宿主校验
scope.plugin_id 与当前 host 一致，并按 scope 构造受限视图，插件无法跨项目读写。
"""
from __future__ import annotations

import inspect
from typing import Any, Iterable

from clipwright.plugins.runtime.protocol import (
    ERR_PERMISSION_DENIED,
    MAX_DEPTH,
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
#: 已加载插件的 host 与 manifest（M5 跨插件调用需要）
_PLUGIN_HOSTS: dict[str, Any] = {}
_PLUGIN_MANIFESTS: dict[str, Any] = {}
#: 任务当前的 Timeline（M6）：渲染路径登记，供 timeline.summary 只读查询
_TIMELINES: dict[str, Any] = {}


def register_timeline(task_id: str, timeline: Any) -> None:
    if task_id:
        _TIMELINES[str(task_id)] = timeline


def unregister_timeline(task_id: str) -> None:
    _TIMELINES.pop(str(task_id or ""), None)
_JOB_MANAGER: PluginJobManager | None = None


#: B5：宿主侧维护的调用链（plugin_id 栈）。由 host 在每次 worker 请求前后压栈/出栈，
#: 插件不能通过 params 伪造或清空。
_CALL_CHAIN: dict[str, list[str]] = {}


def _current_call_chain(plugin_id: str) -> list[str]:
    return list(_CALL_CHAIN.get(str(plugin_id or ""), []))


def push_call_chain(plugin_id: str, caller: str, ancestors: Iterable[str] = ()) -> None:
    """把「祖先链 + 直接调用方」压到目标插件的链上。

    只压直接调用方会丢失祖先（A→B→C 时 C 只能看到 B），深度与环检测形同虚设。
    """
    pid = str(plugin_id or "")
    stack = _CALL_CHAIN.setdefault(pid, [])
    for node in (*ancestors, caller):
        value = str(node or "")
        if value and value not in stack:
            stack.append(value)


def pop_call_chain(plugin_id: str) -> None:
    pid = str(plugin_id or "")
    stack = _CALL_CHAIN.get(pid)
    if stack:
        stack.pop()
        if not stack:
            _CALL_CHAIN.pop(pid, None)


def reset_call_chains() -> None:
    _CALL_CHAIN.clear()


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


def register_plugin_runtime(plugin_id: str, host: Any, manifest: Any = None) -> None:
    """登记插件 host/manifest（loader 加载后调用，M5 使用）。"""
    pid = str(plugin_id or "")
    if not pid:
        return
    _PLUGIN_HOSTS[pid] = host
    if manifest is not None:
        _PLUGIN_MANIFESTS[pid] = manifest


def unregister_plugin_runtime(plugin_id: str) -> None:
    pid = str(plugin_id or "")
    _PLUGIN_HOSTS.pop(pid, None)
    _PLUGIN_MANIFESTS.pop(pid, None)


def install_default_handlers(host: Any, manifest: Any = None) -> None:
    """给一个 PluginHost 注册 workspace/artifact 通用方法。"""
    plugin_id = str(getattr(host, "plugin_id", "") or "")
    register_plugin_runtime(plugin_id, host, manifest)

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

    #: M7：默认放行的引擎能力（只读/计算类）；LLM/搜索等需 manifest 显式授权
    default_capabilities = {"media_probe", "frame_check", "align"}

    async def capability_call(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        _ = parsed
        name = str(params.get("name") or "").strip()
        if not name:
            raise RpcError("BAD_REQUEST", "capability.call 需要 name")
        manifest = _PLUGIN_MANIFESTS.get(plugin_id)
        granted = set((getattr(manifest, "runtime_config", {}) or {}).get("capabilities") or [])
        if name not in default_capabilities and name not in granted:
            raise RpcError(
                ERR_PERMISSION_DENIED,
                f"能力 {name!r} 未授权（默认: {sorted(default_capabilities)}；"
                "其他能力需 manifest.runtime_config.capabilities 声明）",
            )
        from clipwright.tool.capabilities import resolve

        tool = resolve(name)
        if tool is None:
            raise RpcError("UNAVAILABLE", f"当前没有可用的能力实现: {name}")
        payload = dict(params.get("input") or {})
        # B5：不能直接 tool.execute() 绕过统一执行入口（路径/URL 校验、trace、
        # ToolExecResult 语义与 fallback 都在 ToolRegistry.execute 里）。
        from clipwright.tool.registry import ToolRegistry

        result = await ToolRegistry.execute(str(getattr(tool, "name", name)), **payload)
        if result.status == "not_found":
            raise RpcError("UNAVAILABLE", result.error or f"工具不可用: {name}")
        return result.model_dump(mode="json")

    @_wrap
    def timeline_summary(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        """M6：任务时间线只读摘要（不含 clip 明细，明细走 neighbors）。"""
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        timeline = _TIMELINES.get(parsed.task_id)
        if timeline is None:
            raise RpcError("NOT_FOUND", f"任务时间线未登记: {parsed.task_id}")
        tracks = list(getattr(timeline, "tracks", []) or [])
        return {
            "duration_sec": float(getattr(timeline, "duration_sec", 0.0) or 0.0),
            "width": int(getattr(timeline, "width", 0) or 0),
            "height": int(getattr(timeline, "height", 0) or 0),
            "fps": float(getattr(timeline, "fps", 0.0) or 0.0),
            "track_count": len(tracks),
            "clip_count": sum(len(getattr(t, "clips", []) or []) for t in tracks),
        }

    async def plugin_call(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        """M5：跨插件调用（依赖声明 + exposes 白名单 + 链深/环检测）。"""
        parsed = PluginScope.from_payload(scope, expected_plugin=plugin_id)
        target = str(params.get("plugin_id") or "").strip()
        kind = str(params.get("kind") or "tool").strip().lower()
        cid = str(params.get("id") or "").strip()
        # B5：调用链由宿主在入站请求上下文里维护，**不信任 params.chain**。
        # 插件清空/伪造 chain 都无法绕过深度与环检测。
        chain = list(_current_call_chain(plugin_id))
        if not target or not cid or kind not in ("tool", "skill"):
            raise RpcError("BAD_REQUEST",
                           "plugin.call 需要 plugin_id / kind(tool|skill) / id")
        if target == plugin_id or target in chain or len(chain) >= 2:
            raise RpcError(ERR_PERMISSION_DENIED, "插件调用链过深或成环")
        if len(chain) + 1 > MAX_DEPTH:
            raise RpcError(ERR_PERMISSION_DENIED, "插件调用链过深")
        caller_md = _PLUGIN_MANIFESTS.get(plugin_id)
        deps = list(getattr(caller_md, "dependencies", []) or []) if caller_md else []
        if target not in deps:
            raise RpcError(ERR_PERMISSION_DENIED,
                           f"未在 manifest.dependencies 声明目标插件: {target}")
        target_host = _PLUGIN_HOSTS.get(target)
        target_md = _PLUGIN_MANIFESTS.get(target)
        if target_host is None or target_md is None:
            raise RpcError("UNKNOWN_PLUGIN", f"目标插件未加载: {target}")
        exposes = list((getattr(target_md, "runtime_config", {}) or {}).get("exposes") or [])
        if "*" not in exposes and f"{kind}:{cid}" not in exposes:
            raise RpcError(ERR_PERMISSION_DENIED,
                           f"目标插件未暴露 {kind}:{cid}")
        target_scope = {
            "plugin_id": target,
            "project_id": parsed.project_id,
            "task_id": parsed.task_id,
        }
        method = "tool.call" if kind == "tool" else "skill.call"
        # 目标插件在宿主侧链上压入调用方，返回后出栈（不依赖插件回传）。
        push_call_chain(target, plugin_id, chain)
        payload = {
            "id": cid,
            "input": params.get("input") or {},
            "scope": target_scope,
        }
        try:
            result = await target_host.invoke(
                method, payload,
                timeout=float(params.get("timeout") or 120.0),
                scope=target_scope,
            )
        finally:
            pop_call_chain(target)
        return {"plugin_id": target, "kind": kind, "id": cid, "result": result}

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
    host.register_handler("plugin.call", plugin_call)
    host.register_handler("timeline.summary", timeline_summary)
    host.register_handler("capability.call", capability_call)
    # M4：job 取消/超时/任务取消 → 转发为 worker 事件
    job_manager().register_listener(plugin_id, host.notify)
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
