"""插件 RPC 的宿主侧通用 handler（M2）。

只注册**通用**方法：workspace.* / artifact.*。不带任何动效或视频领域参数；
handler 的入参就是路径、内容、role 等通用字段。

每个请求都必须带 scope（plugin_id/project_id/task_id）；宿主校验
scope.plugin_id 与当前 host 一致，并按 scope 构造受限视图，插件无法跨项目读写。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.runtime.protocol import (
    ERR_PERMISSION_DENIED,
    RpcError,
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


def _wrap(fn: Any) -> Any:
    """把服务异常翻译成稳定 RPC 错误码（不把 Python 异常类型漏给插件）。"""

    def _handler(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        try:
            return fn(dict(params or {}), dict(scope or {}))
        except WorkspaceScopeError as e:
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
        return _artifacts(scope).list(
            status=str(params.get("status") or ""),
            role=str(params.get("role") or ""),
        )

    @_wrap
    def artifact_get(params: dict[str, Any], scope: dict[str, Any]) -> Any:
        artifact_id = str(params.get("artifact_id") or "").strip()
        if not artifact_id:
            raise RpcError("BAD_REQUEST", "artifact_id 不能为空")
        return _artifacts(scope).get(artifact_id)

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


__all__ = ["install_default_handlers", "ERR_NOT_FOUND", "ERR_REVISION_CONFLICT"]
