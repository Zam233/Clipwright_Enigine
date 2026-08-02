"""远程渲染 Worker API — 独立部署（默认 0.0.0.0:8100）。

本模块仅提供骨架：全局令牌鉴权 + 健康检查。渲染 / ffmpeg 逻辑由后续 todo
负责，此处刻意保持零渲染实现。后台任务统一走
``clipwright.services.async_util.spawn_background`` / ``asyncio.to_thread``，
绝不在事件循环线程里做同步阻塞调用。
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from clipwright.config import logger


def _resolve_worker_token() -> str:
    """解析 Worker 令牌：优先 ``CLIPWRIGHT_WORKER_TOKEN``，回退 ``CLIPWRIGHT_API_TOKEN``。"""
    token = os.environ.get("CLIPWRIGHT_WORKER_TOKEN")
    if token is None or token == "":
        token = os.environ.get("CLIPWRIGHT_API_TOKEN", "")
    return token


def _bearer_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer 令牌；缺失/格式不符时返回空串。"""
    if not authorization:
        return ""
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):]
    return ""


async def require_worker_auth(authorization: str | None = Header(default=None)) -> None:
    """全局鉴权依赖：令牌模式下校验 ``Authorization: Bearer <token>``。

    与主应用一致使用 ``hmac.compare_digest`` 做恒定时间比较；令牌为空则放行
    （开放开发模式），启动时打印警告。
    """
    token = _resolve_worker_token()
    if not token:
        return
    provided = _bearer_token(authorization)
    if not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的 Worker 令牌")


router = APIRouter(
    prefix="/api/worker",
    tags=["worker"],
    dependencies=[Depends(require_worker_auth)],
)


@router.get("/health")
async def health() -> dict[str, str]:
    """Worker 存活检查。"""
    return {"status": "ok"}


app = FastAPI(
    title="ClipWright Remote Render Worker",
    description="远程渲染 Worker — 独立进程，承担 ffmpeg 渲染任务",
    version="0.1.0",
)

app.include_router(router)

if not _resolve_worker_token():
    logger.warning(
        "安全提示: 未设置 CLIPWRIGHT_WORKER_TOKEN / CLIPWRIGHT_API_TOKEN，"
        "Worker API 处于开放开发模式；生产部署请设置令牌。"
    )
