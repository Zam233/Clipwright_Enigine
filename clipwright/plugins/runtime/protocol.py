"""插件子进程运行时 —— 线协议（P9，v2 双向）。

设计要点：
- **换行分隔 JSON（NDJSON）**：一行一个消息，简单、可流式、便于日志排查；
- 每个请求带自增 `id`，响应回带同一 `id`；v2 起请求增加 `direction`
  （`host` = 宿主发起，`worker` = 插件发起），两个方向各自独立编号，
  响应必须回给发起方向，不能串号；
- 帧大小、并发数与嵌套深度都有上限，防「插件吐超大 JSON / 无限递归打爆宿主」；
- `v` 是协议版本；v1 是单向（仅 host→worker），v2 支持 worker→host 请求。
  宿主同时接受 v1/v2 以便平滑迁移；worker 声明自己的版本，不支持时 fail closed。

消息形态（v2）：
    host 请求    {"v":2,"direction":"host","id":3,"method":"tool.call","params":{...}}
    host 响应    {"v":2,"direction":"host","id":3,"ok":true,"result":{...}}
    worker 请求  {"v":2,"direction":"worker","id":1,"method":"workspace.stat","params":{...}}
    worker 响应  {"v":2,"direction":"worker","id":1,"ok":false,
                  "error":{"code":"SCOPE_DENIED","message":"..."}}
"""
from __future__ import annotations

import json
from typing import Any

#: 当前协议版本
PROTOCOL_VERSION = 2
#: 宿主可兼容的版本集合（v1 仅 host→worker；v2 双向）
SUPPORTED_VERSIONS = (1, 2)

#: 单帧上限（8 MiB）：超过即视为异常插件
MAX_FRAME_BYTES = 8 * 1024 * 1024
#: 单方向在途请求上限：插件的并发申请超过它即拒绝（防打爆宿主）
MAX_INFLIGHT = 32
#: 嵌套调用深度上限（host→worker→host→…）：超过即拒绝
MAX_DEPTH = 8

#: 方向常量
DIRECTION_HOST = "host"
DIRECTION_WORKER = "worker"

#: 错误码（宿主侧按码决策：可重试/直接禁用/仅报错）
ERR_BAD_REQUEST = "BAD_REQUEST"
ERR_UNKNOWN_METHOD = "UNKNOWN_METHOD"
ERR_PERMISSION_DENIED = "PERMISSION_DENIED"
ERR_PLUGIN_ERROR = "PLUGIN_ERROR"
ERR_TIMEOUT = "TIMEOUT"
ERR_WORKER_DEAD = "WORKER_DEAD"
ERR_BOOT_FAILED = "BOOT_FAILED"
ERR_UNSUPPORTED_KIND = "UNSUPPORTED_KIND"
#: v2 新增
ERR_OVERLOADED = "OVERLOADED"
ERR_DEPTH_EXCEEDED = "DEPTH_EXCEEDED"
ERR_CANCELLED = "CANCELLED"
ERR_SCOPE_DENIED = "SCOPE_DENIED"
ERR_UNSUPPORTED_METHOD = "UNSUPPORTED_METHOD"


class RpcError(Exception):
    """worker 侧/宿主侧统一错误载体。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or ERR_PLUGIN_ERROR)
        self.message = str(message or "")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, data: Any) -> "RpcError":
        if isinstance(data, dict):
            return cls(str(data.get("code") or ERR_PLUGIN_ERROR),
                       str(data.get("message") or ""))
        return cls(ERR_PLUGIN_ERROR, str(data))


def encode(message: dict[str, Any]) -> bytes:
    """消息 → 单行字节（含换行）。超限直接拒绝，避免把坏帧写进管道。"""
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    raw = payload.encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise RpcError(ERR_BAD_REQUEST, f"帧超过上限 {MAX_FRAME_BYTES} 字节")
    return raw + b"\n"


def decode(line: bytes | str) -> dict[str, Any]:
    """单行 → 消息 dict；非法 JSON/非对象一律 RpcError（调用方决定是否拆掉该插件）。

    B2.2：入站字节**在 decode 之前**就按 MAX_FRAME_BYTES 限长（含多字节 UTF-8，
    按字节数算），避免恶意/失控 worker 用超长行把宿主内存打爆。
    """
    if isinstance(line, bytes):
        # 去掉行尾换行后按「内容字节数」判定：MAX_FRAME_BYTES 是单帧内容上限，
        # 允许再多 1 字节的 '\n'（宿主 readline 也按 MAX_FRAME_BYTES+1 截断）。
        payload = line[:-1] if line.endswith(b"\n") else line
        if len(payload) > MAX_FRAME_BYTES:
            raise RpcError(ERR_BAD_REQUEST, f"入站帧超过上限 {MAX_FRAME_BYTES} 字节")
        line = line.decode("utf-8", errors="replace")
    else:
        payload = line[:-1] if line.endswith("\n") else line
        if len(payload.encode("utf-8")) > MAX_FRAME_BYTES:
            raise RpcError(ERR_BAD_REQUEST, f"入站帧超过上限 {MAX_FRAME_BYTES} 字节")
    text = line.strip()
    if not text:
        raise RpcError(ERR_BAD_REQUEST, "空帧")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RpcError(ERR_BAD_REQUEST, f"非法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise RpcError(ERR_BAD_REQUEST, "帧必须是 JSON 对象")
    return data


def request(
    req_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    direction: str = DIRECTION_HOST,
    deadline_ms: int | None = None,
    parent_call_id: int | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一个请求帧（v2）。v1 兼容：不带 direction 时按 host 方向处理。"""
    message: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "direction": str(direction),
        "id": int(req_id),
        "method": str(method),
        "params": params or {},
    }
    if deadline_ms is not None:
        message["deadline_ms"] = int(deadline_ms)
    if parent_call_id is not None:
        message["parent_call_id"] = int(parent_call_id)
    if scope is not None:
        message["scope"] = dict(scope)
    return message


def ok(req_id: int, result: Any, *, direction: str = DIRECTION_HOST) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "direction": str(direction),
        "id": int(req_id),
        "ok": True,
        "result": result,
    }


def fail(
    req_id: int,
    error: RpcError,
    *,
    direction: str = DIRECTION_HOST,
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "direction": str(direction),
        "id": int(req_id),
        "ok": False,
        "error": error.to_dict(),
    }


def check_version(message: dict[str, Any]) -> None:
    """校验协议版本（握手与每条消息都走这里）。

    v1 消息没有 `direction`，语义等价于 host→worker；v2 必须带方向。
    兼容集合之外的版本直接拒绝，不做"半懂不懂"降级。
    """
    version = message.get("v")
    if version not in SUPPORTED_VERSIONS:
        raise RpcError(
            ERR_BAD_REQUEST,
            f"协议版本不匹配：收到 {version!r}，宿主支持 {list(SUPPORTED_VERSIONS)}",
        )


def event_frame(name: str, payload: dict[str, Any] | None = None,
                *, direction: str = DIRECTION_HOST) -> dict[str, Any]:
    """host→worker 的单向事件（进度/取消）。不占请求 id，不需要响应。"""
    return {
        "v": PROTOCOL_VERSION,
        "direction": str(direction),
        "kind": "event",
        "event": str(name),
        "payload": payload or {},
    }


def is_event(message: dict[str, Any]) -> bool:
    return message.get("kind") == "event" and "event" in message


def is_request(message: dict[str, Any]) -> bool:
    """请求帧：带 method、不带 ok 字段。"""
    return "method" in message and "ok" not in message


def is_response(message: dict[str, Any]) -> bool:
    """响应帧：带 ok 字段。"""
    return "ok" in message


def direction_of(message: dict[str, Any]) -> str:
    """归一化方向：v1 消息没有 direction，按 host 方向处理。"""
    value = str(message.get("direction") or DIRECTION_HOST)
    if value not in (DIRECTION_HOST, DIRECTION_WORKER):
        raise RpcError(ERR_BAD_REQUEST, f"非法 direction: {value!r}")
    return value
