"""worker 侧 v2 读循环：同时处理宿主请求与插件请求的响应。

为什么单独成文件：v1 的 `_serve` 是「读一行 → await handle → 写一行」，
当插件在 handler 里 `await call_host(...)` 时，读循环正卡在 handle 上，
宿主的响应永远读不到 → 死锁。v2 必须把读循环与请求处理拆开：

    stdin 读循环 ──┬─ 响应帧 → 唤醒 _WorkerRpc 的 pending
                   └─ 请求帧 → 独立 task → runtime.handle → 回包

写管道统一走 `_WorkerRpc.send`（带锁），避免请求与响应交错成坏帧。
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from typing import Any

from clipwright.plugins.runtime import bridge
from clipwright.plugins.runtime.protocol import (
    DIRECTION_WORKER,
    ERR_OVERLOADED,
    ERR_PLUGIN_ERROR,
    ERR_TIMEOUT,
    ERR_WORKER_DEAD,
    MAX_INFLIGHT,
    RpcError,
    check_version,
    decode,
    direction_of,
    encode,
    fail,
    is_event,
    is_request,
    is_response,
    ok,
    request,
)


class WorkerRpc:
    """worker 侧传输客户端：发请求、解析响应、限流。"""

    def __init__(self, out: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._out = out
        self._loop = loop
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._inflight = 0

    async def send(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            self._out.write(encode(message))
            self._out.flush()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
        scope: dict[str, Any] | None = None,
    ) -> Any:
        if self._inflight >= MAX_INFLIGHT:
            raise RpcError(ERR_OVERLOADED, f"插件在途请求超过上限 {MAX_INFLIGHT}")
        req_id = self._next
        self._next += 1
        fut: asyncio.Future = self._loop.create_future()
        self._pending[req_id] = fut
        self._inflight += 1
        try:
            await self.send(
                request(req_id, method, params or {},
                        direction=DIRECTION_WORKER, scope=scope)
            )
            return await asyncio.wait_for(fut, timeout=float(timeout))
        except asyncio.TimeoutError:
            raise RpcError(ERR_TIMEOUT, f"等待宿主 {method} 超时（{timeout}s）") from None
        finally:
            self._inflight -= 1
            self._pending.pop(req_id, None)

    def resolve(self, message: dict[str, Any]) -> None:
        req_id = int(message.get("id", -1))
        fut = self._pending.pop(req_id, None)
        if fut is None or fut.done():
            return
        if message.get("ok"):
            fut.set_result(message.get("result"))
        else:
            fut.set_exception(RpcError.from_dict(message.get("error")))

    def fail_all(self, code: str, message: str) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(RpcError(code, message))


async def _handle_request(runtime: Any, rpc: WorkerRpc,
                          message: dict[str, Any]) -> None:
    """处理一条宿主请求：执行 → 回包；异常必须变成错误帧。"""
    req_id = int(message.get("id", -1))
    try:
        direction = direction_of(message)
    except RpcError:
        direction = "host"
    # scope 随请求下发；插件在 handler 内 call_host 时默认沿用，宿主据此校验归属
    token = bridge.set_scope(dict(message.get("scope") or {}))
    try:
        try:
            result = await runtime.handle(
                str(message.get("method") or ""), dict(message.get("params") or {})
            )
            await rpc.send(ok(req_id, result, direction=direction))
        except RpcError as e:
            await rpc.send(fail(req_id, e, direction=direction))
        except Exception as e:  # noqa: BLE001 — 插件异常必须回成错误帧而非崩掉 worker
            traceback.print_exc(file=sys.stderr)
            await rpc.send(fail(req_id, RpcError(ERR_PLUGIN_ERROR, str(e)),
                                direction=direction))
    finally:
        bridge.reset_scope(token)


async def serve(runtime: Any, out: Any,
                loop: asyncio.AbstractEventLoop) -> int:
    """NDJSON 读循环（v2 双向）。返回进程退出码。"""
    rpc = WorkerRpc(out, loop)
    bridge.bind(rpc)
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
            if not line:
                return 0  # 宿主关闭管道 → 正常退出
            try:
                message = decode(line)
                check_version(message)
            except RpcError as e:
                sys.stderr.write(f"[worker] 非法帧: {e.code} {e.message}\n")
                continue
            if is_event(message):
                bridge.emit_event(
                    str(message.get("event") or ""),
                    dict(message.get("payload") or {}),
                )
                continue
            if is_response(message):
                rpc.resolve(message)
                continue
            if not is_request(message):
                sys.stderr.write("[worker] 忽略无法识别的帧\n")
                continue
            if message.get("method") == "shutdown":
                await rpc.send(ok(int(message.get("id", -1)), {"bye": True}))
                return 0
            task = loop.create_task(_handle_request(runtime, rpc, message))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        bridge.unbind()
        for task in tasks:
            if not task.done():
                task.cancel()
        rpc.fail_all(ERR_WORKER_DEAD, "worker 关闭")


__all__ = ["WorkerRpc", "serve"]
