"""远程渲染 Worker API — 独立部署（默认 0.0.0.0:8100）。

提供：全局令牌鉴权 + 健康检查 + 渲染素材（asset）存储接口（上传 / 去重 /
存在性探测 / 下载）+ 远程渲染任务（jobs）接口（提交 / 状态 / 下载产物）。
渲染 / ffmpeg 逻辑由 ``clipwright.worker.render_runner.run_job`` 复用主应用
RenderService 完成。后台任务统一走 ``clipwright.services.async_util.spawn_background``
/ ``asyncio.to_thread``，绝不在事件循环线程里做同步阻塞调用。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from clipwright.config import logger
from clipwright.services.async_util import spawn_background
from clipwright.worker.render_runner import run_job
from clipwright.worker.store import store


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

# ---- 渲染素材存储 ----------------------------------------------

# 默认工作目录：<项目根>/_cache/worker（项目根 = clipwright 包的父目录）
_DEFAULT_WORK_DIR = Path(__file__).resolve().parent.parent.parent / "_cache" / "worker"

# 默认单文件上传上限 2GB，可用 CLIPWRIGHT_WORKER_MAX_ASSET_MB（MB）覆盖
_DEFAULT_MAX_ASSET_MB = 2048

# 扩展名净化：仅保留 [A-Za-z0-9.]，杜绝任何路径成分混入落盘名
_ASSET_EXT_RE = re.compile(r"[^A-Za-z0-9.]")

# 流式拷贝分块大小（shutil.copyfileobj 每次读写的字节数）
_COPY_CHUNK_BYTES = 1024 * 1024


def _work_dir() -> Path:
    """解析工作目录：优先 CLIPWRIGHT_WORKER_WORK_DIR，否则回退默认。"""
    env_dir = os.environ.get("CLIPWRIGHT_WORKER_WORK_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir)
    return _DEFAULT_WORK_DIR


def _max_asset_bytes() -> int:
    """解析单文件上传上限（字节）；非法配置回退默认 2GB。"""
    raw = os.environ.get("CLIPWRIGHT_WORKER_MAX_ASSET_MB")
    if raw is not None and raw.strip() != "":
        try:
            mb = int(raw.strip())
        except ValueError:
            logger.warning("CLIPWRIGHT_WORKER_MAX_ASSET_MB 非整数: %r，回退默认 2GB", raw)
            mb = _DEFAULT_MAX_ASSET_MB
        return max(0, mb) * 1024 * 1024
    return _DEFAULT_MAX_ASSET_MB * 1024 * 1024


def _assets_dir() -> Path:
    """返回素材目录并确保其存在（按需创建）。"""
    assets = _work_dir() / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    return assets


def _sanitized_ext(filename: str | None) -> str:
    """仅取客户端文件名的后缀并净化到 [A-Za-z0-9.]；绝不使用原始路径。"""
    if not filename:
        return ""
    suffix = Path(filename).suffix
    return _ASSET_EXT_RE.sub("", suffix)[:16]


def _asset_path(hash_short: str, ext: str) -> Path:
    """素材落盘路径：<work_dir>/assets/<sha1[:16]><ext>。"""
    return _work_dir() / "assets" / f"{hash_short}{ext}"


def _find_asset_path(asset_hash: str) -> Path | None:
    """按 sha1（16 位前缀）查找素材文件；不存在或非法输入返回 None。"""
    prefix = (asset_hash or "")[:16].lower()
    if not re.fullmatch(r"[0-9a-f]{16}", prefix):
        return None
    assets = _work_dir() / "assets"
    if not assets.is_dir():
        return None
    for p in assets.iterdir():
        if p.is_file() and p.name.startswith(prefix):
            return p
    return None


class _HashingWriter:
    """流式写入代理：逐块增量更新 sha1 并执行体积上限检查（内存 O(chunk)）。"""

    def __init__(self, target: BinaryIO, size_limit: int) -> None:
        self._target = target
        self._size_limit = size_limit
        self._size = 0
        self._digest = hashlib.sha1()

    def write(self, chunk: bytes) -> int:
        self._size += len(chunk)
        if self._size > self._size_limit:
            raise HTTPException(status_code=413, detail="文件超过大小上限")
        self._digest.update(chunk)
        return self._target.write(chunk)

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


@router.get("/health")
async def health() -> dict[str, str]:
    """Worker 存活检查。"""
    return {"status": "ok"}


@router.post("/assets")
async def upload_asset(
    file: UploadFile = File(...),
    client_hash: str | None = Form(default=None, alias="hash"),
) -> dict:
    """上传渲染素材：服务端增量计算 sha1，按 <sha1[:16]><ext> 落盘并去重。

    - ``hash``（可选表单字段）：客户端预计算的 sha1 hex；与服务端计算值不一致
      时返回 409 ``{"detail": "hash mismatch"}``。
    - 已存在同名素材时返回 ``{"hash": ..., "stored": false}``（去重，不重复写盘）。
    - 流式写盘（shutil.copyfileobj 分块），绝不把整个文件读进内存。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = _sanitized_ext(file.filename)
    assets_dir = _assets_dir()
    max_bytes = _max_asset_bytes()

    tmp_path = assets_dir / f".upload-{os.getpid()}-{id(file)}"
    try:
        with open(tmp_path, "wb") as raw:
            writer = _HashingWriter(raw, max_bytes)
            shutil.copyfileobj(file.file, writer, length=_COPY_CHUNK_BYTES)

        full_hash = writer.hexdigest
        if client_hash is not None and client_hash.lower() != full_hash:
            raise HTTPException(status_code=409, detail="hash mismatch")

        stored_path = _asset_path(full_hash[:16], ext)
        if stored_path.exists():
            return {"hash": full_hash, "stored": False}

        os.replace(tmp_path, stored_path)
        return {"hash": full_hash, "stored": True}
    finally:
        tmp_path.unlink(missing_ok=True)


@router.head("/assets/{asset_hash}")
async def asset_exists(asset_hash: str) -> None:
    """素材存在性探测（HEAD，去重前探）。存在返回 200，否则 404。"""
    if _find_asset_path(asset_hash) is None:
        raise HTTPException(status_code=404, detail="Asset not found")


@router.get("/assets/{asset_hash}")
async def get_asset(asset_hash: str) -> FileResponse:
    """下载素材（对称/调试用）。存在返回文件，否则 404。"""
    path = _find_asset_path(asset_hash)
    if path is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(path)


# ---- 远程渲染任务（jobs）-----------------------------------------------

def _parse_job_request(body: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """校验并拆解 jobs 请求体 → ``(timeline, params, asset_refs)``。

    - ``timeline`` 必须是含 ``tracks`` 列表的对象；
    - ``params`` / ``asset_refs``（可选）必须是对象；
    - ``asset_refs`` 的值必须是 ``asset://`` 开头的字符串。
    任一不合法即抛 400。
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    timeline = body.get("timeline")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("tracks"), list):
        raise HTTPException(status_code=400, detail="timeline 必须是包含 tracks 列表的对象")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params 必须是对象")
    asset_refs = body.get("asset_refs") or {}
    if not isinstance(asset_refs, dict):
        raise HTTPException(status_code=400, detail="asset_refs 必须是对象")
    for aid, uri in asset_refs.items():
        if not isinstance(uri, str) or not uri.startswith("asset://"):
            raise HTTPException(
                status_code=400,
                detail=f"asset_refs[{aid!r}] 必须是 asset:// 开头的字符串",
            )
    return timeline, params, asset_refs


@router.post("/jobs", status_code=202)
async def create_render_job(body: dict) -> dict:
    """提交远程渲染任务：校验后创建 job，立即返回 ``{"job_id": ...}``（202）。

    后台协程执行 :func:`clipwright.worker.render_runner.run_job`；run_job 内部已
    负责写 failed/error，这里再包一层 try/except 兜底，确保任何未预期异常都不会
    留下永远卡在 queued/rendering 的僵死 job。
    """
    timeline, params, asset_refs = _parse_job_request(body)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    store.create_job(job_id)
    logger.info("Worker job 提交: %s (tracks=%d)", job_id, len(timeline.get("tracks", [])))

    async def _run() -> None:
        try:
            await run_job(job_id, timeline, params, asset_refs, store)
        except Exception as e:
            logger.exception("Worker job 执行异常: %s", job_id)
            store.update_job(job_id, status="failed", error=str(e))

    spawn_background(_run(), name=f"worker-job-{job_id}")
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """查询任务状态（status/progress/phase/detail/error/output_path）；不存在 404。"""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return {"job_id": job_id, **job}


@router.get("/jobs/{job_id}/download")
async def download_job_output(job_id: str) -> FileResponse:
    """下载已完成的渲染产物 MP4；任务未完成返回 409，任务不存在返回 404。"""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="job not completed")
    output_path = job.get("output_path") or ""
    if not output_path or not Path(output_path).is_file():
        raise HTTPException(status_code=404, detail="output file not found")
    return FileResponse(output_path, media_type="video/mp4", filename=f"{job_id}.mp4")


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
