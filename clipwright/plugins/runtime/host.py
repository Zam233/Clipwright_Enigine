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
    ERR_BOOT_FAILED,
    ERR_TIMEOUT,
    ERR_WORKER_DEAD,
    PROTOCOL_VERSION,
    RpcError,
    decode,
    encode,
    request,
)

#: 单次调用默认超时（秒）；hook/工具共用，可由 manifest.runtime_config 覆盖
DEFAULT_CALL_TIMEOUT = 30.0
#: 启动与 describe 的握手超时
BOOT_TIMEOUT = 20.0


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
    ) -> None:
        self.plugin_id = str(plugin_id)
        self.plugin_dir = Path(plugin_dir)
        self.data_dir = Path(data_dir)
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
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
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

    # ── RPC ──────────────────────────────────────────

    async def invoke(self, method: str, params: dict[str, Any],
                     timeout: float = 0.0) -> Any:
        """异步调用点用：把阻塞 RPC 交给线程池，不占宿主事件循环。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self.invoke_sync, method, params, timeout,
        )

    def invoke_sync(self, method: str, params: dict[str, Any],
                    timeout: float = 0.0) -> Any:
        """同步调用点（如 Hook）用：阻塞等待结果。"""
        limit = float(timeout or self.call_timeout)
        return self._submit(self._rpc(method, params, limit),
                            timeout=limit + 5.0, what=method)

    async def _rpc(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        if not self.alive:
            raise RpcError(ERR_WORKER_DEAD, self._dead_reason or "worker 未启动或已退出")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = fut
            self._write(request(req_id, method, params))
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
        """后台读循环：按 id 把响应派发给等待中的 future。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    message = decode(line)
                except RpcError as e:
                    logger.warning("子进程插件 %s 回帧非法: %s", self.plugin_id, e.message)
                    continue
                req_id = int(message.get("id", -1))
                fut = self._pending.pop(req_id, None)
                if fut is None or fut.done():
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


def runtime_available() -> bool:
    """子进程运行时是否可用（用于能力展示与错误信息）。"""
    return bool(sys.executable) and PROTOCOL_VERSION > 0


__all__ = ["PluginHost", "DEFAULT_CALL_TIMEOUT", "BOOT_TIMEOUT", "runtime_available"]
