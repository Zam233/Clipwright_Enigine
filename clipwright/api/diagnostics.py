"""前端崩溃上报（诊断用）。

为什么要有这个（2026-10-31）：用户连续两次报同一个前端崩溃
`Objects are not valid as a React child (found: object with keys {name})`，
但只贴了那一行 message——没有组件栈就无从定位，而"让用户手动复制控制台"
既不现实也不可靠。

这个端点让浏览器**自己把崩溃写进后端日志**：前端 ErrorBoundary / window.onerror
捕获后 POST 过来，落到 `logs/client-errors.jsonl`。排障时直接读文件即可，
不需要用户配合、不需要复现步骤描述。

红线：
- 不鉴权（崩溃上报必须在任何状态下都能写出去），但**只写日志**、不接受任何执行语义；
- 每条记录有硬上限（字段截断 + 单文件轮转），防止被刷；
- 任何写入失败都返回 204，绝不因为日志写不进去影响前端。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FILE = _LOG_DIR / "client-errors.jsonl"
_MAX_BYTES = 4 * 1024 * 1024     # 4MB 后轮转到 .1（只保留一代）
_MAX_FIELD = 8000


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:_MAX_FIELD]


def _rotate() -> None:
    try:
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > _MAX_BYTES:
            backup = _LOG_FILE.with_suffix(".jsonl.1")
            _LOG_FILE.replace(backup)
    except Exception:  # noqa: BLE001 - 轮转失败不能影响上报
        pass


@router.post("/client-error")
async def client_error(request: Request) -> Response:
    """接收浏览器崩溃记录（幂等、尽力而为，永远 204）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 非法 body 也照样回 204
        return Response(status_code=204)
    if not isinstance(body, dict):
        return Response(status_code=204)

    record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "route": _clip(body.get("route", "")),
        "name": _clip(body.get("name", "")),
        "message": _clip(body.get("message", "")),
        "stack": _clip(body.get("stack", "")),
        "component_stack": _clip(body.get("componentStack", "")),
        "source": _clip(body.get("source", "")),        # boundary | window | unhandledrejection
        "ua": _clip(body.get("ua", "")),
        "extra": _clip(body.get("extra", "")),
    }
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate()
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return Response(status_code=204)


@router.get("/client-errors")
async def list_client_errors(limit: int = 20) -> dict[str, Any]:
    """最近的前端崩溃（倒序；给排障/管理页用）。"""
    limit = max(1, min(int(limit or 20), 200))
    if not _LOG_FILE.exists():
        return {"items": [], "path": str(_LOG_FILE)}
    try:
        lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:  # noqa: BLE001
        return {"items": [], "path": str(_LOG_FILE)}
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    items.reverse()
    return {"items": items, "path": str(_LOG_FILE)}
