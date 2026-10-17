"""插件子进程运行时 —— 线协议（P9）。

设计要点：
- **换行分隔 JSON（NDJSON）**：一行一个消息，简单、可流式、便于日志排查；
- 每个请求带自增 `id`，响应回带同一 `id`（宿主可并发多请求、按 id 配对）；
- 帧大小上限防「插件吐超大 JSON 打爆宿主内存」；
- `v` 字段是协议版本，握手不一致直接拒绝（宁可拒载也不要半懂不懂地跑）。

消息形态：
    请求  {"v":1,"id":3,"method":"tool.call","params":{"id":"my_tool","input":{...}}}
    成功  {"v":1,"id":3,"ok":true,"result":{...}}
    失败  {"v":1,"id":3,"ok":false,"error":{"code":"PERMISSION_DENIED","message":"..."}}
"""
from __future__ import annotations

import json
from typing import Any

#: 协议版本（宿主与 worker 必须一致）
PROTOCOL_VERSION = 1

#: 单帧上限（8 MiB）：超过即视为异常插件
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: 错误码（宿主侧按码决策：可重试/直接禁用/仅报错）
ERR_BAD_REQUEST = "BAD_REQUEST"
ERR_UNKNOWN_METHOD = "UNKNOWN_METHOD"
ERR_PERMISSION_DENIED = "PERMISSION_DENIED"
ERR_PLUGIN_ERROR = "PLUGIN_ERROR"
ERR_TIMEOUT = "TIMEOUT"
ERR_WORKER_DEAD = "WORKER_DEAD"
ERR_BOOT_FAILED = "BOOT_FAILED"
ERR_UNSUPPORTED_KIND = "UNSUPPORTED_KIND"


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
    """单行 → 消息 dict；非法 JSON/非对象一律 RpcError（调用方决定是否拆掉该插件）。"""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
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


def request(req_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": int(req_id), "method": str(method),
            "params": params or {}}


def ok(req_id: int, result: Any) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": int(req_id), "ok": True, "result": result}


def fail(req_id: int, error: RpcError) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": int(req_id), "ok": False, "error": error.to_dict()}


def check_version(message: dict[str, Any]) -> None:
    """校验协议版本（握手与每条消息都走这里）。"""
    version = message.get("v")
    if version != PROTOCOL_VERSION:
        raise RpcError(
            ERR_BAD_REQUEST,
            f"协议版本不匹配：worker={version} host={PROTOCOL_VERSION}",
        )
