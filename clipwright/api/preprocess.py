"""素材预处理 API — 提交和管理素材预处理任务。

预处理包括:
  ・场景检测 (scene detection)
  ・缩略图 / 预览帧生成
  ・元数据提取 (分辨率、帧率、时长、编码)
  ・音频提取 + BPM 检测
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger

router = APIRouter(prefix="/api/preprocess", tags=["preprocess"])


# ── 枚举 & 模型 ───────────────────────────────


class PreprocessStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PreprocessTask(BaseModel):
    """预处理任务。"""
    task_id: str = ""
    file_path: str = Field(description="源文件路径")
    file_name: str = ""
    operations: list[str] = Field(description="预处理操作列表")
    status: PreprocessStatus = PreprocessStatus.QUEUED
    progress: float = Field(default=0, description="进度 0-100")
    results: dict[str, Any] = Field(default_factory=dict, description="各操作的结果")
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""


class SubmitRequest(BaseModel):
    """提交预处理请求。"""
    file_path: str = Field(description="视频文件路径")
    operations: list[str] = Field(
        default=["metadata", "scenes", "thumbnail"],
        description="预处理操作: metadata/scenes/thumbnail/audio/bpm",
    )


class BatchSubmitRequest(BaseModel):
    """批量提交预处理请求。"""
    file_paths: list[str] = Field(description="视频文件路径列表")
    operations: list[str] = Field(
        default=["metadata", "scenes", "thumbnail"],
        description="预处理操作列表",
    )


# 支持的操作
SUPPORTED_OPERATIONS = ["metadata", "scenes", "thumbnail", "audio", "bpm"]


# ── 内存任务队列 ───────────────────────────────

_tasks: dict[str, dict[str, Any]] = {}
_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_running = False


# ── API 端点 ───────────────────────────────────


@router.get("/operations")
async def list_operations() -> dict:
    """列出支持的预处理操作。"""
    return {
        "operations": SUPPORTED_OPERATIONS,
        "descriptions": {
            "metadata": "提取视频元数据 (分辨率、帧率、时长、编码格式)",
            "scenes": "场景检测 (基于内容变化的切点检测)",
            "thumbnail": "生成缩略图和预览帧序列",
            "audio": "提取音频轨道",
            "bpm": "音频节拍检测 (BPM)",
        },
    }


@router.get("/queue", response_model=list[PreprocessTask])
async def preprocess_queue(status: str = "") -> list[PreprocessTask]:
    """查看预处理队列，可按状态过滤。"""
    tasks = list(_tasks.values())
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    # 按创建时间排序
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return [PreprocessTask(**t) for t in tasks]


@router.post("/submit", response_model=PreprocessTask)
async def submit_task(req: SubmitRequest) -> PreprocessTask:
    """提交单个文件的预处理任务。"""
    # 验证操作
    invalid = [op for op in req.operations if op not in SUPPORTED_OPERATIONS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported operations: {invalid}. Supported: {SUPPORTED_OPERATIONS}",
        )

    # 验证文件存在
    file_path = Path(req.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {req.file_path}")

    # 安全：仅允许白名单目录内的素材，防止任意路径喂 ffmpeg/ffprobe/whisper
    from clipwright.security import assert_allowed_path
    assert_allowed_path(file_path)

    task_id = f"pp_{uuid.uuid4().hex[:10]}"
    now = datetime.now(tz=TIME_ZONE).isoformat()

    task = {
        "task_id": task_id,
        "file_path": req.file_path,
        "file_name": file_path.name,
        "operations": req.operations,
        "status": PreprocessStatus.QUEUED.value,
        "progress": 0,
        "results": {},
        "error": "",
        "created_at": now,
        "started_at": "",
        "completed_at": "",
    }

    _tasks[task_id] = task
    await _queue.put(task_id)

    # 确保 worker 在运行
    _ensure_worker()

    logger.info("预处理任务已提交: %s (%s, ops=%s)", task_id, file_path.name, req.operations)
    return PreprocessTask(**task)


@router.post("/batch-submit", response_model=list[PreprocessTask])
async def batch_submit(req: BatchSubmitRequest) -> list[PreprocessTask]:
    """批量提交预处理任务。"""
    invalid = [op for op in req.operations if op not in SUPPORTED_OPERATIONS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported operations: {invalid}. Supported: {SUPPORTED_OPERATIONS}",
        )

    results: list[PreprocessTask] = []
    from clipwright.security import SecurityViolation, assert_allowed_path
    for fp in req.file_paths:
        file_path = Path(fp)
        if not file_path.exists():
            continue
        try:
            assert_allowed_path(file_path)
        except SecurityViolation:
            continue

        task_id = f"pp_{uuid.uuid4().hex[:10]}"
        now = datetime.now(tz=TIME_ZONE).isoformat()

        task = {
            "task_id": task_id,
            "file_path": fp,
            "file_name": file_path.name,
            "operations": req.operations,
            "status": PreprocessStatus.QUEUED.value,
            "progress": 0,
            "results": {},
            "error": "",
            "created_at": now,
            "started_at": "",
            "completed_at": "",
        }
        _tasks[task_id] = task
        await _queue.put(task_id)
        results.append(PreprocessTask(**task))

    _ensure_worker()

    logger.info("批量预处理已提交: %d 个文件", len(results))
    return results


@router.get("/task/{task_id}", response_model=PreprocessTask)
async def get_task(task_id: str) -> PreprocessTask:
    """查询预处理任务状态。"""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return PreprocessTask(**task)


@router.delete("/task/{task_id}")
async def cancel_task(task_id: str) -> dict:
    """取消预处理任务（仅限排队中的任务）。"""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    if task["status"] != PreprocessStatus.QUEUED.value:
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel task in '{task['status']}' state"
        )

    task["status"] = PreprocessStatus.FAILED.value
    task["error"] = "Cancelled by user"
    task["completed_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    return {"status": "cancelled", "task_id": task_id}


@router.get("/task/{task_id}/results")
async def get_task_results(task_id: str) -> dict:
    """获取预处理结果。"""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    if task["status"] != PreprocessStatus.COMPLETED.value:
        return {
            "task_id": task_id,
            "status": task["status"],
            "message": "Task not completed yet",
        }

    return {
        "task_id": task_id,
        "status": task["status"],
        "results": task["results"],
    }


# ── 后台 Worker ───────────────────────────────


def _ensure_worker() -> None:
    """确保后台 worker 在运行。"""
    global _worker_running
    if _worker_running:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop and loop.is_running():
            loop.create_task(_worker())
            _worker_running = True
    except RuntimeError:
        pass


async def _worker() -> None:
    """后台预处理 worker — 从队列取任务并执行。"""
    global _worker_running
    logger.debug("预处理 worker 已启动")

    while True:
        try:
            task_id = await asyncio.wait_for(_queue.get(), timeout=60)
        except asyncio.TimeoutError:
            # 队列空闲超过 60 秒，退出 worker
            _worker_running = False
            logger.debug("预处理 worker 空闲退出")
            return

        task = _tasks.get(task_id)
        if task is None or task["status"] != PreprocessStatus.QUEUED.value:
            continue

        task["status"] = PreprocessStatus.RUNNING.value
        task["started_at"] = datetime.now(tz=TIME_ZONE).isoformat()

        try:
            results = await _execute_preprocess(task)
            task["results"] = results
            task["status"] = PreprocessStatus.COMPLETED.value
            task["progress"] = 100
        except Exception as e:
            task["status"] = PreprocessStatus.FAILED.value
            task["error"] = str(e)[:500]
            logger.warning("预处理任务失败 %s: %s", task_id, e)

        task["completed_at"] = datetime.now(tz=TIME_ZONE).isoformat()


async def _execute_preprocess(task: dict) -> dict[str, Any]:
    """执行预处理操作。"""
    from clipwright.tool.registry import ToolRegistry

    file_path = task["file_path"]
    operations = task["operations"]
    results: dict[str, Any] = {}
    total = len(operations)

    for i, op in enumerate(operations):
        task["progress"] = round((i / total) * 100, 1)

        if op == "metadata":
            results["metadata"] = await _extract_metadata(file_path)

        elif op == "scenes":
            try:
                scene_result = await ToolRegistry.execute("scene_detect", input_path=file_path)
                results["scenes"] = scene_result
            except Exception as e:
                results["scenes"] = {"error": str(e)}

        elif op == "thumbnail":
            results["thumbnail"] = await _generate_thumbnail(file_path)

        elif op == "audio":
            try:
                audio_result = await ToolRegistry.execute("audio_extract", input_path=file_path)
                results["audio"] = audio_result
            except Exception as e:
                results["audio"] = {"error": str(e)}

        elif op == "bpm":
            try:
                bpm_result = await ToolRegistry.execute("bpm_detect", input_path=file_path)
                results["bpm"] = bpm_result
            except Exception as e:
                results["bpm"] = {"error": str(e)}

    return results


async def _extract_metadata(file_path: str) -> dict[str, Any]:
    """使用 ffprobe 提取视频元数据。"""
    import subprocess

    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            file_path,
        ]
        proc = await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        )
        if proc.returncode == 0:
            info = json.loads(proc.stdout)
            fmt = info.get("format", {})
            video_stream = next(
                (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
            )
            return {
                "duration_sec": float(fmt.get("duration", 0)),
                "size_bytes": int(fmt.get("size", 0)),
                "format": fmt.get("format_name", ""),
                "width": int(video_stream.get("width", 0)),
                "height": int(video_stream.get("height", 0)),
                "fps": _parse_fps(video_stream.get("r_frame_rate", "0/1")),
                "codec": video_stream.get("codec_name", ""),
            }
        return {"error": f"ffprobe failed: {proc.stderr[:200]}"}
    except Exception as e:
        return {"error": str(e)}


async def _generate_thumbnail(file_path: str) -> dict[str, Any]:
    """生成缩略图（取第 1 秒的帧）。"""
    import subprocess
    import tempfile

    try:
        out_fd, out_path_str = tempfile.mkstemp(suffix=".jpg")
        os.close(out_fd)
        out_path = Path(out_path_str)
        cmd = [
            "ffmpeg", "-y", "-ss", "1", "-i", file_path,
            "-vframes", "1", "-q:v", "3",
            "-vf", "scale=320:-1",
            str(out_path),
        ]
        proc = await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        )
        if proc.returncode == 0 and out_path.exists():
            return {"path": str(out_path), "size_bytes": out_path.stat().st_size}
        return {"error": f"ffmpeg failed: {proc.stderr[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _parse_fps(fps_str: str) -> float:
    """解析 ffprobe 的帧率字符串 (如 '30000/1001')。"""
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return round(int(num) / max(int(den), 1), 2)
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 0.0
