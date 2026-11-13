"""子进程插件宿主（P9）——启动 worker、RPC 路由、超时与崩溃语义。

线程/循环模型（重要）：worker 的管道 I/O 必须只有**一个**事件循环在管，
否则跨循环对 future `set_result` 会抛 RuntimeError。因此本类自建一个常驻
线程事件循环（`_loop`），启动、关闭与每一次 RPC 都汇入它：

    async 调用点 → invoke()      → run_in_executor(线程池) ┐
    同步调用点   → invoke_sync() ─────────────────────────┴→ 线程循环 → 管道

安全属性（这才是「子进程」买到的东西，声明校验买不到）：
1. **宿主不 import 插件代码**：插件在独立进程里加载，宿主 `sys.modules` 里没有它；
2. **进程内审计钩子**：worker 启动即装 `PermissionGuard`，越权在发生瞬间被拒；
3. **资源边界**：调用有超时、帧有大小上限、worker 崩溃只影响自己的贡献。

崩溃语义（fail-closed，不静默）：worker 退出/超时 → 该插件所有在途与后续调用
一律抛 `RpcError`（带可读原因），绝不返回空结果骗过上层。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.plugins.runtime.protocol import (
    DIRECTION_HOST,
    DIRECTION_WORKER,
    ERR_BOOT_FAILED,
    ERR_OVERLOADED,
    ERR_TIMEOUT,
    ERR_UNKNOWN_METHOD,
    MAX_FRAME_BYTES,
    check_version,
    ERR_WORKER_DEAD,
    MAX_INFLIGHT,
    PROTOCOL_VERSION,
    RpcError,
    decode,
    direction_of,
    encode,
    event_frame,
    fail,
    is_event,
    is_request,
    is_response,
    ok,
    request,
)

#: 单次调用默认超时（秒）；hook/工具共用，可由 manifest.runtime_config 覆盖
DEFAULT_CALL_TIMEOUT = 120.0
#: 启动与 describe 的握手超时
BOOT_TIMEOUT = 60.0

#: 传给 worker 的环境变量白名单（M1）：只保留运行必需项，
#: **显式剥离** CLIPWRIGHT_*、模型/云厂商密钥、代理凭据等。
ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL",
    "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH",
)


class PluginHost:
    """一个插件一个子进程的宿主。

    `invoke` 供 async 调用点、`invoke_sync` 供同步调用点（如 Hook）；两者最终
    都汇入同一个线程事件循环，worker 单管道串行收发不会被并发打乱。
    """

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        data_dir: Path,
        *,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        python_executable: str = "",
        extra_read_roots: Optional[list[str]] = None,
        plugin_config: Optional[dict[str, Any]] = None,
    ) -> None:
        self.plugin_id = str(plugin_id)
        self.plugin_dir = Path(plugin_dir)
        self.data_dir = Path(data_dir)
        # 宿主解密后的配置（子进程不再自己读密文文件）
        self.plugin_config: dict[str, Any] = dict(plugin_config or {})
        # 宿主审核后的额外读根（审计 P1：worker 不能从 manifest 自报字段取，
        # 否则子进程可读任意目录）。通过启动环境变量下发。
        self.extra_read_roots = [str(p) for p in (extra_read_roots or [])]
        # 最近一次宿主→worker 调用的可信 scope（project/task 归属只能由宿主给，
        # 不能在 worker 回传的 call_host scope 里被改写）。
        self._trusted_scope: dict[str, Any] = {}
        self.call_timeout = float(call_timeout or DEFAULT_CALL_TIMEOUT)
        self._python = python_executable or sys.executable
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._dead_reason = ""
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix=f"plug-{self.plugin_id}",
        )
        self.declarations: list[dict[str, Any]] = []
        # v2：worker 主动请求的处理表（method → handler）与在途任务
        self._handlers: dict[str, Any] = {}
        self._worker_tasks: set[asyncio.Task] = set()
        self._worker_inflight = 0

    # ── 生命周期 ─────────────────────────────────────

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self._dead_reason

    def start(self, timeout: float = BOOT_TIMEOUT) -> list[dict[str, Any]]:
        """同步启动（加载器是同步流程）：起 worker、握手、取回贡献声明。"""
        try:
            return self._submit(self._start(), timeout=timeout, what="启动")
        except RpcError:
            self.stop()
            raise
        except Exception as e:  # noqa: BLE001 — 启动失败统一转可读 RpcError
            self.stop()
            raise RpcError(ERR_BOOT_FAILED, f"子进程插件启动失败: {e}") from e

    async def _start(self) -> list[dict[str, Any]]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # **强制子进程 UTF-8 I/O**（`-X utf8` + PYTHONIOENCODING）。
        #
        # 与 worker 写二进制 stdout 是同一处缺陷的两道防线：worker 自己写的是
        # `sys.stdout.buffer`（不受 locale 影响），但**插件代码**可能直接 `print()` 中文日志、
        # 或让异常 traceback（走文本模式 stderr）带上中文——那些路径由子进程的
        # locale 编码决定，宿主却固定按 UTF-8 解码（`_drain_stderr`），
        # 中文 Windows 上（cp936）同样会损坏甚至抛 UnicodeDecodeError。
        # 实测：不加 `-X utf8` 时，worker 回传 `{"b": "两"}` 的字节是 `\xc1\xbd`。
        cmd = [
            self._python, "-X", "utf8",
            "-m", "clipwright.plugins.runtime.worker",
            str(self.plugin_dir), self.plugin_id, str(self.data_dir),
        ]
        # M1：不再继承宿主全环境（旧实现 dict(os.environ) 会把
        # CLIPWRIGHT_API_TOKEN / 模型密钥直接交给插件进程）。
        env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        # 额外读根只由宿主下发（JSON 数组）；空数组也要显式给，worker 不再读
        # manifest 里的自报字段。
        import json as _json
        env["CLIPWRIGHT_PLUGIN_READ_ROOTS"] = _json.dumps(self.extra_read_roots)
        if self.plugin_config:
            env["CLIPWRIGHT_PLUGIN_CONFIG"] = _json.dumps(
                self.plugin_config, ensure_ascii=False, default=str,
            )
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            # B2：StreamReader 行缓冲上限抬到协议帧上限，否则 64KiB 的默认值会把
            # 合法大结果（如 70KB 工具输出）当成超长帧而杀掉 worker。
            limit=MAX_FRAME_BYTES + 1,
        )
        self._reader_task = asyncio.get_running_loop().create_task(self._read_responses())
        self._stderr_task = asyncio.get_running_loop().create_task(self._drain_stderr())
        result = await self._rpc("describe", {}, timeout=BOOT_TIMEOUT)
        self.declarations = list((result or {}).get("contributions") or [])
        logger.info("子进程插件 %s 已就绪：%d 项贡献（pid=%s）",
                    self.plugin_id, len(self.declarations),
                    self._proc.pid if self._proc else "?")
        return self.declarations

    def stop(self, timeout: float = 5.0) -> None:
        """同步关闭：尽力 shutdown → 等退出 → 超时 kill。可重复调用。"""
        proc = self._proc
        if proc is None:
            self._pool.shutdown(wait=False)
            return
        try:
            self._submit(self._stop(timeout), timeout=timeout + 2.0, what="关闭")
        except Exception as e:  # noqa: BLE001 — 关闭失败不能抛给调用方
            logger.warning("子进程插件 %s 关闭异常: %s", self.plugin_id, e)
        finally:
            self._proc = None
            self._pool.shutdown(wait=False)

    async def _stop(self, timeout: float) -> None:
        proc, self._proc = self._proc, None
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._reader_task = None
        self._stderr_task = None
        if proc is None:
            return
        # 先给插件一次 shutdown 钩子（审计 P2：旧实现直接关管道，插件清理不执行）
        try:
            await asyncio.wait_for(self._rpc("shutdown", {}, 2.0), timeout=3.0)
        except Exception:  # noqa: BLE001 — 旧 worker 可能不支持/已死
            pass
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._fail_pending(ERR_WORKER_DEAD, "worker 已关闭")
        logger.info("子进程插件 %s 已停止", self.plugin_id)

    def mark_dead(self, reason: str) -> None:
        """标记 worker 不可用（崩溃/超时后调用），在途请求立即失败。"""
        if not self._dead_reason:
            self._dead_reason = str(reason or "worker 不可用")
            logger.warning("子进程插件 %s 不可用: %s", self.plugin_id, self._dead_reason)
        self._fail_pending(ERR_WORKER_DEAD, self._dead_reason)

    # ── v2：worker → host 请求 ────────────────────────

    def notify(self, name: str, payload: dict[str, Any] | None = None) -> bool:
        """向 worker 推送一条单向事件（M4）；worker 不可用时返回 False。"""
        if not self.alive:
            return False
        try:
            self._write(event_frame(name, payload or {}))
            return True
        except Exception as e:  # noqa: BLE001 — 通知失败不应影响业务
            logger.debug("插件 %s 事件推送失败 %s: %s", self.plugin_id, name, e)
            return False

    def register_handler(self, method: str, handler: Any) -> None:
        """注册一个可被 worker 调用的宿主方法。

        handler 可以是同步或异步可调用，签名 `handler(params, scope) -> Any`。
        M2 之后由 workspace/artifact/job 服务注册；M1 只验证通道本身。
        """
        name = str(method or "").strip()
        if not name:
            raise ValueError("handler method 不能为空")
        self._handlers[name] = handler

    async def _dispatch_worker_request(self, message: dict[str, Any]) -> None:
        """处理 worker 发来的请求：限流 → 查表 → 执行 → 回包（方向 worker）。"""
        req_id = int(message.get("id", -1))
        method = str(message.get("method") or "")
        params = dict(message.get("params") or {})
        scope = dict(message.get("scope") or {})
        depth = int(message.get("depth", 0) or 0)
        # 归属只能由宿主给：worker 自报的 project/task/plugin 必须与本次调用的
        # 可信 scope 一致，不一致按伪造拒绝（审计 P1：旧实现只看 plugin_id，
        # 子进程可读写任意已知 project 的素材/产物）。
        trusted = getattr(self, "_trusted_scope", None) or {}
        try:
            for key in ("project_id", "task_id"):
                claimed = scope.get(key)
                if trusted.get(key):
                    if claimed not in (None, "", trusted.get(key)):
                        raise RpcError(
                            "PERMISSION_DENIED",
                            f"scope.{key} 与宿主调用上下文不一致（疑似伪造）",
                        )
                    scope[key] = trusted[key]
            claimed_plugin = str(scope.get("plugin_id") or "")
            if claimed_plugin and claimed_plugin != self.plugin_id:
                raise RpcError("PERMISSION_DENIED", "scope.plugin_id 与当前插件不一致")
            scope["plugin_id"] = self.plugin_id
            if depth > 8:
                raise RpcError("DEPTH_EXCEEDED", f"嵌套过深（depth={depth}）")
            if self._worker_inflight >= MAX_INFLIGHT:
                raise RpcError(ERR_OVERLOADED, f"在途请求超过上限 {MAX_INFLIGHT}")
            handler = self._handlers.get(method)
            if handler is None:
                raise RpcError(ERR_UNKNOWN_METHOD, f"宿主不支持方法: {method}")
            self._worker_inflight += 1
            try:
                result = handler(params, scope)
                if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                    result = await asyncio.wait_for(result, timeout=self.call_timeout)
            finally:
                self._worker_inflight -= 1
            self._write(ok(req_id, result, direction=DIRECTION_WORKER))
        except asyncio.TimeoutError:
            self._write(fail(req_id, RpcError(ERR_TIMEOUT, f"{method} 超时"),
                             direction=DIRECTION_WORKER))
        except RpcError as e:
            self._write(fail(req_id, e, direction=DIRECTION_WORKER))
        except Exception as e:  # noqa: BLE001 — 单个 handler 异常不能拆掉插件
            logger.warning("插件 %s 的宿主方法 %s 异常: %s", self.plugin_id, method, e)
            self._write(fail(req_id, RpcError("PLUGIN_ERROR", str(e)),
                             direction=DIRECTION_WORKER))

    # ── RPC ──────────────────────────────────────────

    async def invoke(self, method: str, params: dict[str, Any],
                     timeout: float = 0.0,
                     scope: dict[str, Any] | None = None) -> Any:
        """异步调用点用：把阻塞 RPC 交给线程池，不占宿主事件循环。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self.invoke_sync, method, params, timeout, scope,
        )

    def invoke_sync(self, method: str, params: dict[str, Any],
                    timeout: float = 0.0,
                    scope: dict[str, Any] | None = None) -> Any:
        """同步调用点（如 Hook）用：阻塞等待结果。

        `scope` 随请求下发到 worker，插件的 `call_host` 默认沿用它，
        宿主据此做 plugin/task/project 归属校验（M2）。
        """
        limit = float(timeout or self.call_timeout)
        if scope:
            self._trusted_scope = dict(scope)
        return self._submit(self._rpc(method, params, limit, scope),
                            timeout=limit + 5.0, what=method)

    async def _rpc(self, method: str, params: dict[str, Any], timeout: float,
                   scope: dict[str, Any] | None = None) -> Any:
        if not self.alive:
            raise RpcError(ERR_WORKER_DEAD, self._dead_reason or "worker 未启动或已退出")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = fut
            self._write(request(req_id, method, params, scope=scope))
        try:
            return await asyncio.wait_for(fut, timeout=float(timeout))
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            self.mark_dead(f"{method} 超时（{timeout}s）")
            raise RpcError(ERR_TIMEOUT,
                           f"插件 {self.plugin_id} 的 {method} 超时（{timeout}s）") from None

    # ── 内部：线程循环 ────────────────────────────────

    def _submit(self, coro: Any, *, timeout: float, what: str) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            self.mark_dead(f"{what} 超时")
            raise RpcError(ERR_TIMEOUT,
                           f"插件 {self.plugin_id} 的 {what} 超时") from None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever,
                name=f"plug-loop-{self.plugin_id}",
                daemon=True,
            ).start()
            self._loop = loop
            return loop

    def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RpcError(ERR_WORKER_DEAD, "worker 未启动")
        try:
            proc.stdin.write(encode(message))
        except (BrokenPipeError, ConnectionResetError) as e:
            self.mark_dead(f"写入管道失败: {e}")
            raise RpcError(ERR_WORKER_DEAD, self._dead_reason) from e

    async def _read_responses(self) -> None:
        """后台读循环：响应按 id 唤醒 pending；请求交给 handler 异步任务。

        v2 关键点：worker 可能在**等待宿主响应**（例如 workspace.stat），
        所以这里不能用同一个 await 链处理请求后再读下一行——请求必须
        派发给独立 task，读循环立即回到 readline。
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                # B2：readline() 默认 StreamReader 上限 64KiB，70KB 的合法结果会被
                # LimitOverrunError 直接判死 worker。改用 readuntil 并把 limit 抬到
                # 协议帧上限，超长帧只丢该帧（回错误），不拆掉整个插件进程。
                try:
                    line = await proc.stdout.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    logger.warning(
                        "子进程插件 %s 发来超长帧（>%d 字节），已丢弃该帧",
                        self.plugin_id, MAX_FRAME_BYTES,
                    )
                    # 吃掉剩余数据到下一个换行，保持后续帧可解析
                    try:
                        await proc.stdout.readuntil(b"\n")
                    except Exception:  # noqa: BLE001 — 已经无法对齐则退出循环
                        break
                    continue
                except asyncio.IncompleteReadError as e:
                    line = e.partial
                    if not line:
                        break
                if not line:
                    break
                try:
                    message = decode(line)
                    # B2：每条入站帧都校验协议版本——旧实现只在握手看一次，
                    # worker 后续发 v3 或省略 v 都会被当成合法消息处理。
                    check_version(message)
                    direction = direction_of(message)
                except RpcError as e:
                    logger.warning("子进程插件 %s 回帧非法: %s", self.plugin_id, e.message)
                    # 版本/方向错说明对端实现不可信，继续读只会收到更多坏帧：
                    # 标记死亡并结束读循环，让调用方拿到明确错误而不是半可用状态。
                    self.mark_dead(f"协议帧非法: {e.message}")
                    break

                if is_request(message):
                    if direction != DIRECTION_WORKER:
                        logger.warning("插件 %s 发来 host 方向的请求帧，已忽略",
                                       self.plugin_id)
                        continue
                    task = asyncio.get_running_loop().create_task(
                        self._dispatch_worker_request(message)
                    )
                    self._worker_tasks.add(task)
                    task.add_done_callback(self._worker_tasks.discard)
                    continue

                if not is_response(message) or direction != DIRECTION_HOST:
                    logger.warning("插件 %s 发来无法识别的帧（direction=%s）",
                                   self.plugin_id, direction)
                    continue

                req_id = int(message.get("id", -1))
                fut = self._pending.pop(req_id, None)
                if fut is None or fut.done():
                    logger.debug("插件 %s 的未知/已结束响应 id=%s，已忽略",
                                 self.plugin_id, req_id)
                    continue
                if message.get("ok"):
                    fut.set_result(message.get("result"))
                else:
                    err = message.get("error") or {}
                    fut.set_exception(RpcError(
                        str(err.get("code") or "PLUGIN_ERROR"),
                        str(err.get("message") or ""),
                    ))
        except asyncio.CancelledError:  # pragma: no cover - 正常关闭路径
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("子进程插件 %s 读循环异常: %s", self.plugin_id, e)
        finally:
            code = None if proc is None else proc.returncode
            self.mark_dead(f"worker 退出（exit={code}）")

    async def _drain_stderr(self) -> None:
        """把 worker stderr 收进日志（插件 print/异常栈留痕，不污染 stdout 协议）。"""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.info("[plugin:%s] %s", self.plugin_id, text)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001
            pass

    def _fail_pending(self, code: str, message: str) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if fut.done():
                continue
            try:
                fut.set_exception(RpcError(code, message))
            except Exception:  # noqa: BLE001 - future 可能已取消
                pass
        # v2：在途的 worker 请求处理任务一并取消，避免 worker 死后仍在等回包
        tasks, self._worker_tasks = self._worker_tasks, set()
        for task in tasks:
            if not task.done():
                task.cancel()


def runtime_available() -> bool:
    """子进程运行时是否可用（用于能力展示与错误信息）。"""
    return bool(sys.executable) and PROTOCOL_VERSION > 0


__all__ = ["PluginHost", "DEFAULT_CALL_TIMEOUT", "BOOT_TIMEOUT", "runtime_available"]
