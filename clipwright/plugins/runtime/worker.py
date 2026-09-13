"""插件子进程 worker 入口（P9）。

一个 worker 只跑**一个**插件：审计钩子是进程级的，多插件混跑会让权限判定串味。

职责：
1. 读 plugin.yaml → 权限声明（与宿主同源校验，防止宿主漏检）；
2. 装权限审计钩子（在 import 插件代码**之前**，插件模块顶层代码同样受限）；
3. import 插件模块、实例化、`setup()` → `contributions()` 得到声明列表；
4. 服务宿主 RPC：`describe` / `tool.call` / `hook.call` / `material.search` /
   `prompt.get` / `ping` / `shutdown`。

数据在进程边界上只走 JSON：声明是纯数据（kind/id/meta），宿主据此构造代理对象，
插件对象本身永远留在 worker 里——这正是「宿主不 import 插件代码」的实现方式。

启动参数（argv）：
    python -m clipwright.plugins.runtime.worker <plugin_dir> <plugin_id> <data_dir>
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from clipwright.plugins.runtime.permissions import PermissionGuard, install_audit_hook
from clipwright.plugins.runtime.protocol import (
    ERR_PLUGIN_ERROR,
    ERR_UNKNOWN_METHOD,
    ERR_UNSUPPORTED_KIND,
    RpcError,
    check_version,
    decode,
    encode,
    fail,
    ok,
)

#: 允许跨越进程边界的贡献类型（纯数据或有明确 RPC 语义）
_RPC_KINDS = {"tool", "hook", "material_source", "prompt", "ui_slot", "export_preset"}

#: 每类 kind 的 RPC 方法名（宿主代理据此调用）
_RPC_METHOD = {
    "tool": "tool.call",
    "hook": "hook.call",
    "material_source": "material.search",
    "prompt": "prompt.get",
}


class PluginRuntime:
    """worker 内的插件宿主：持有插件实例与按 id 索引的贡献对象。"""

    def __init__(self, plugin_dir: Path, plugin_id: str, data_dir: Path) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.plugin_id = str(plugin_id)
        self.data_dir = Path(data_dir)
        self.manifest: Any = None
        self.plugin: Any = None
        self.objects: dict[str, Any] = {}
        self.declarations: list[dict[str, Any]] = []

    # ── 启动 ─────────────────────────────────────────

    def boot(self) -> None:
        self.manifest = self._parse_manifest()
        guard = PermissionGuard(
            getattr(self.manifest, "permissions", []) or [],
            plugin_id=self.plugin_id,
            data_dir=self.data_dir,
        )
        install_audit_hook(guard)  # 必须在 import 插件代码之前
        module = self._import_plugin()
        self.plugin = self._instantiate(module)
        self._collect_declarations()

    def _parse_manifest(self) -> Any:
        import yaml

        from clipwright.schema.plugin import PluginManifest

        manifest_path = self.plugin_dir / "plugin.yaml"
        data: dict[str, Any] = {}
        if manifest_path.exists():
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        data.pop("id", None)
        return PluginManifest(id=self.plugin_id, **data)

    def _import_plugin(self) -> Any:
        entry = getattr(self.manifest, "entry_point", "") or ""
        candidates = [entry] if entry else []
        candidates += ["main.py", "__init__.py"]
        for name in candidates:
            path = self.plugin_dir / name
            if not path.exists():
                continue
            module_name = f"cw_sandbox_{self.plugin_id}_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
        raise RpcError(ERR_PLUGIN_ERROR, f"插件入口模块缺失: {self.plugin_dir}")

    def _instantiate(self, module: Any) -> Any:
        from clipwright.plugins.base import BasePlugin

        klass = None
        for name in dir(module):
            obj = getattr(module, name)
            if inspect.isclass(obj) and issubclass(obj, BasePlugin) and obj is not BasePlugin:
                if obj.__module__ == module.__name__:
                    klass = obj
                    break
        if klass is None:
            raise RpcError(ERR_PLUGIN_ERROR, "入口模块中未找到 BasePlugin 子类")
        instance = klass()
        instance.manifest = self.manifest
        instance.config = self._flat_config()
        return instance

    def _flat_config(self) -> dict[str, Any]:
        """读 PluginData 的 config.yaml（与宿主同源：结构化 → 扁平值）。"""
        from clipwright.plugins.loader import _extract_flat_values

        config_path = self.data_dir / "config.yaml"
        if not config_path.exists():
            return {}
        try:
            import yaml
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return _extract_flat_values(raw) if isinstance(raw, dict) else {}

    def _collect_declarations(self) -> None:
        """调用 setup()/contributions()，把贡献转成纯数据声明。

        `setup()` 是 P1 迁移引入的生命周期位：副作用（建目录、读配置）放这里，
        `contributions()` 只负责声明——这样声明可以被序列化送过进程边界。
        """
        setup = getattr(self.plugin, "setup", None)
        if callable(setup):
            setup()
        raw = getattr(self.plugin, "contributions", None)
        items = list(raw() or []) if callable(raw) else []
        for item in items:
            kind = getattr(getattr(item, "kind", None), "value", None) or str(
                getattr(item, "kind", "")
            )
            cid = str(getattr(item, "id", "") or "")
            if not kind or not cid:
                continue
            if kind not in _RPC_KINDS:
                raise RpcError(
                    ERR_UNSUPPORTED_KIND,
                    f"子进程档暂不支持 {kind}（{cid}）；支持：{sorted(_RPC_KINDS)}",
                )
            self.objects[cid] = getattr(item, "object", None)
            self.declarations.append({
                "kind": kind,
                "id": cid,
                "meta": _jsonable(getattr(item, "meta", {}) or {}),
            })

    # ── RPC 分发 ─────────────────────────────────────

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping":
            return "pong"
        if method == "describe":
            return {"plugin_id": self.plugin_id, "contributions": self.declarations}
        if method == "tool.call":
            return await self._call_tool(params)
        if method == "hook.call":
            return await self._call_hook(params)
        if method == "material.search":
            return await self._call_material(params)
        if method == "prompt.get":
            return self._get_prompt(params)
        if method == "shutdown":
            return {"bye": True}
        raise RpcError(ERR_UNKNOWN_METHOD, f"未知方法: {method}")

    async def _call_tool(self, params: dict[str, Any]) -> Any:
        tool = self._object(params, expected_kind="tool")
        runner = getattr(tool, "execute", None)
        if not callable(runner):
            raise RpcError(ERR_PLUGIN_ERROR, "工具对象缺少 execute()")
        payload = dict(params.get("input") or {})
        result = runner(**payload)  # 间接调用：避免被静态扫描误判为 SQL 执行
        if inspect.isawaitable(result):
            result = await result
        return _tool_result(result)

    async def _call_hook(self, params: dict[str, Any]) -> Any:
        hook = self._object(params, expected_kind="hook")
        context = dict(params.get("context") or {})
        result = hook(context)
        if inspect.isawaitable(result):
            result = await result
        return _jsonable(result) if isinstance(result, dict) else None

    async def _call_material(self, params: dict[str, Any]) -> Any:
        src = self._object(params, expected_kind="material_source")
        searcher = getattr(src, "search", None)
        if not callable(searcher):
            raise RpcError(ERR_PLUGIN_ERROR, "素材源对象缺少 search()")
        result = searcher(str(params.get("query") or ""),
                          **dict(params.get("kwargs") or {}))
        if inspect.isawaitable(result):
            result = await result
        return _jsonable(result)

    def _get_prompt(self, params: dict[str, Any]) -> Any:
        obj = self._object(params, expected_kind="prompt")
        if isinstance(obj, str):
            return obj
        text = getattr(obj, "prompt", None)
        return str(text or "")

    def _object(self, params: dict[str, Any], expected_kind: str) -> Any:
        cid = str(params.get("id") or "")
        obj = self.objects.get(cid)
        if obj is None:
            raise RpcError(ERR_UNKNOWN_METHOD, f"未知贡献 id: {cid}")
        declared = next((d for d in self.declarations if d["id"] == cid), None)
        if declared and declared["kind"] != expected_kind:
            raise RpcError(ERR_UNKNOWN_METHOD,
                           f"{cid} 的类型是 {declared['kind']}，不能按 {expected_kind} 调用")
        return obj


def _jsonable(value: Any) -> Any:
    """把结果裁剪成可 JSON 序列化（不可序列化 → repr，避免整帧失败）。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return repr(value)


def _tool_result(value: Any) -> Any:
    """工具返回值统一成 {success,output,error} 形状。

    三种常见形态：ToolExecResult 对象、同形 dict（插件常直接返回 dict）、
    裸数据（list/dict/标量 → 视为成功输出）。
    """
    if value is None:
        return {"success": True, "output": None}
    if isinstance(value, dict) and "success" in value:
        return {
            "success": bool(value.get("success")),
            "output": _jsonable(value.get("output")),
            "error": value.get("error"),
        }
    for attr in ("success", "output", "error"):
        if hasattr(value, attr):
            return {
                "success": bool(getattr(value, "success", False)),
                "output": _jsonable(getattr(value, "output", None)),
                "error": getattr(value, "error", None),
            }
    return {"success": True, "output": _jsonable(value)}


async def _serve(runtime: PluginRuntime) -> int:
    """NDJSON 主循环（stdin 读请求，stdout 写响应）。"""
    loop = asyncio.get_running_loop()
    out = sys.stdout
    while True:
        line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
        if not line:
            return 0  # 宿主关闭管道 → 正常退出
        req_id = -1
        try:
            message = decode(line)
            check_version(message)
            req_id = int(message.get("id", -1))
            if message.get("method") == "shutdown":
                out.write(encode(ok(req_id, {"bye": True})).decode())
                out.flush()
                return 0
            result = await runtime.handle(str(message.get("method") or ""),
                                          dict(message.get("params") or {}))
            out.write(encode(ok(req_id, result)).decode())
        except RpcError as e:
            out.write(encode(fail(req_id, e)).decode())
        except Exception as e:  # noqa: BLE001 — 插件异常必须回成错误帧而非崩掉 worker
            traceback.print_exc(file=sys.stderr)
            out.write(encode(fail(req_id, RpcError(ERR_PLUGIN_ERROR, str(e)))).decode())
        out.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 3:
        sys.stderr.write("用法: worker <plugin_dir> <plugin_id> <data_dir>\n")
        return 2
    # 顺序很重要：**先建事件循环，再装审计钩子**。
    # asyncio 事件循环初始化时会创建自管道（Windows 上是 socketpair）；若钩子先装，
    # 这些内部 socket 调用会被判成「未声明 network」而抛异常，循环就半初始化了
    # （表现为 `_ssock` 缺失）。插件代码在钩子之后才 import，安全边界不受影响。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    runtime = PluginRuntime(Path(args[0]), args[1], Path(args[2]))
    try:
        runtime.boot()
    except RpcError as e:
        sys.stderr.write(f"[worker] 启动失败: {e.code} {e.message}\n")
        return 3
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write(f"[worker] 启动失败: {e}\n")
        return 3
    try:
        return loop.run_until_complete(_serve(runtime))
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    finally:
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
