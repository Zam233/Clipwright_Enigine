"""学习/微调管线 API — LoRA 微调任务管理。

支持:
  ・提交训练数据集（视频 + 标注）
  ・启动 / 停止微调任务
  ・查询训练进度和指标
  ・管理已训练的模型
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger
from clipwright.security import validate_id
from clipwright.services.async_util import cached_probe, run_blocking


def _probe_gpu() -> bool:
    """同步 nvidia-smi 探测（后台线程执行，不进事件循环线程）。"""
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# GPU 可用性缓存 120s，后台刷新，await 永不阻塞事件循环。
_gpu_available = cached_probe("nvidia_smi", _probe_gpu, ttl=120.0, default=False)


async def _check_gpu() -> bool:
    """async GPU 检测：命中缓存立即返回，失效后台刷新，不阻塞事件循环。"""
    return bool(await _gpu_available())

router = APIRouter(prefix="/api/learning", tags=["learning"])


async def _guard_dataset_id(dataset_id: str | None = None) -> None:
    """路由级守卫：dataset_id 出现在路径中时校验合法性（防路径遍历）。"""
    if dataset_id is not None:
        validate_id(dataset_id, "dataset_id")


router.dependencies = [Depends(_guard_dataset_id)]

# 训练任务存储目录
_LEARNING_DIR = Path("learning")
_MODELS_DIR = _LEARNING_DIR / "models"
_DATASETS_DIR = _LEARNING_DIR / "datasets"


# ── 枚举 & 模型 ───────────────────────────────


class TrainingStatus(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    TRAINING = "training"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingJob(BaseModel):
    """训练任务。"""
    job_id: str = ""
    name: str = ""
    status: TrainingStatus = TrainingStatus.PENDING
    base_model: str = Field(default="", description="基础模型")
    dataset_id: str = Field(default="", description="数据集 ID")
    config: dict[str, Any] = Field(default_factory=dict, description="训练超参数")
    progress: float = Field(default=0, description="进度 0-100")
    current_epoch: int = 0
    total_epochs: int = 0
    metrics: dict[str, float] = Field(default_factory=dict, description="训练指标")
    output_model_id: str = Field(default="", description="输出模型 ID")
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""


class DatasetInfo(BaseModel):
    """数据集信息。"""
    dataset_id: str = ""
    name: str = ""
    description: str = ""
    sample_count: int = 0
    total_duration_sec: float = 0
    created_at: str = ""
    status: str = "ready"


class CreateJobRequest(BaseModel):
    """创建训练任务请求。"""
    name: str = Field(description="任务名称")
    base_model: str = Field(default="default", description="基础模型标识")
    dataset_id: str = Field(description="数据集 ID")
    epochs: int = Field(default=10, ge=1, le=100)
    learning_rate: float = Field(default=1e-4, gt=0)
    batch_size: int = Field(default=4, ge=1, le=64)
    lora_rank: int = Field(default=8, ge=4, le=128)
    extra_config: dict[str, Any] = Field(default_factory=dict)


class CreateDatasetRequest(BaseModel):
    """创建数据集请求。"""
    name: str = Field(description="数据集名称")
    description: str = Field(default="")
    video_paths: list[str] = Field(default_factory=list, description="视频文件路径列表")
    annotations: list[dict[str, Any]] = Field(default_factory=list, description="标注数据")


# ── API 端点 ───────────────────────────────────


@router.get("/status")
async def learning_status() -> dict:
    """学习管线整体状态。"""
    await run_blocking(_LEARNING_DIR.mkdir, parents=True, exist_ok=True)
    jobs = await run_blocking(_load_jobs)
    active = [j for j in jobs if j["status"] in ("training", "preparing", "evaluating")]
    return {
        "status": "training" if active else "idle",
        "active_jobs": len(active),
        "total_jobs": len(jobs),
        "gpu_available": await _check_gpu(),
    }


# ── 数据集管理 ─────────────────────────────────


@router.get("/datasets", response_model=list[DatasetInfo])
async def list_datasets() -> list[DatasetInfo]:
    """列出所有数据集。"""
    await run_blocking(_DATASETS_DIR.mkdir, parents=True, exist_ok=True)
    datasets: list[DatasetInfo] = []
    for f in sorted(_DATASETS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            datasets.append(DatasetInfo(**data.get("meta", {})))
        except Exception:
            continue
    return datasets


@router.post("/datasets/create", response_model=DatasetInfo)
async def create_dataset(req: CreateDatasetRequest) -> DatasetInfo:
    """创建训练数据集。"""
    await run_blocking(_DATASETS_DIR.mkdir, parents=True, exist_ok=True)

    dataset_id = f"ds_{uuid.uuid4().hex[:10]}"
    now = datetime.now(tz=TIME_ZONE).isoformat()

    meta = {
        "dataset_id": dataset_id,
        "name": req.name,
        "description": req.description,
        "sample_count": len(req.video_paths),
        "total_duration_sec": 0,
        "created_at": now,
        "status": "ready",
    }

    data = {
        "meta": meta,
        "video_paths": req.video_paths,
        "annotations": req.annotations,
    }

    path = _DATASETS_DIR / f"{dataset_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("数据集已创建: %s (%s, %d samples)", dataset_id, req.name, len(req.video_paths))
    return DatasetInfo(**meta)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict:
    """删除数据集。"""
    path = _DATASETS_DIR / f"{dataset_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    path.unlink()
    return {"status": "deleted", "dataset_id": dataset_id}


# ── 训练任务管理 ───────────────────────────────


@router.get("/jobs", response_model=list[TrainingJob])
async def list_jobs(status: str = "") -> list[TrainingJob]:
    """列出训练任务，可按状态过滤。"""
    jobs = await run_blocking(_load_jobs)
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    return [TrainingJob(**j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=TrainingJob)
async def get_job(job_id: str) -> TrainingJob:
    """获取训练任务详情。"""
    jobs = await run_blocking(_load_jobs)
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return TrainingJob(**job)


@router.post("/jobs/create", response_model=TrainingJob)
async def create_job(req: CreateJobRequest) -> TrainingJob:
    """创建训练任务（排队等待执行）。"""
    validate_id(req.dataset_id, "dataset_id")
    await run_blocking(_LEARNING_DIR.mkdir, parents=True, exist_ok=True)

    # 验证数据集存在
    ds_path = _DATASETS_DIR / f"{req.dataset_id}.json"
    if not ds_path.exists():
        raise HTTPException(status_code=400, detail=f"Dataset '{req.dataset_id}' not found")

    job_id = f"train_{uuid.uuid4().hex[:10]}"
    now = datetime.now(tz=TIME_ZONE).isoformat()

    job = {
        "job_id": job_id,
        "name": req.name,
        "status": TrainingStatus.PENDING.value,
        "base_model": req.base_model,
        "dataset_id": req.dataset_id,
        "config": {
            "epochs": req.epochs,
            "learning_rate": req.learning_rate,
            "batch_size": req.batch_size,
            "lora_rank": req.lora_rank,
            **req.extra_config,
        },
        "progress": 0,
        "current_epoch": 0,
        "total_epochs": req.epochs,
        "metrics": {},
        "output_model_id": "",
        "error": "",
        "created_at": now,
        "started_at": "",
        "completed_at": "",
    }

    jobs = await run_blocking(_load_jobs)
    jobs.append(job)
    await run_blocking(_save_jobs, jobs)

    logger.info("训练任务已创建: %s (%s, %d epochs)", job_id, req.name, req.epochs)
    return TrainingJob(**job)


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: str) -> dict:
    """启动训练任务。

    注意: 当前为 Phase 5 规划功能，实际训练执行需要 GPU 环境。
    此端点将任务状态标记为 training，实际训练逻辑待后续实现。
    """
    jobs = await run_blocking(_load_jobs)
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job["status"] not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail=f"Job is already {job['status']}")

    if not await _check_gpu():
        raise HTTPException(
            status_code=503,
            detail="GPU not available. LoRA training requires CUDA GPU.",
        )

    job["status"] = TrainingStatus.TRAINING.value
    job["started_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    await run_blocking(_save_jobs, jobs)

    logger.info("训练任务已启动: %s", job_id)
    return {"status": "started", "job_id": job_id, "message": "Training started (Phase 5 - execution pending)"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    """取消训练任务。"""
    jobs = await run_blocking(_load_jobs)
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job["status"] in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Job is already {job['status']}")

    job["status"] = TrainingStatus.CANCELLED.value
    job["completed_at"] = datetime.now(tz=TIME_ZONE).isoformat()
    await run_blocking(_save_jobs, jobs)

    return {"status": "cancelled", "job_id": job_id}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    """删除训练任务记录。"""
    jobs = await run_blocking(_load_jobs)
    before = len(jobs)
    jobs = [j for j in jobs if j["job_id"] != job_id]
    if len(jobs) == before:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    await run_blocking(_save_jobs, jobs)
    return {"status": "deleted", "job_id": job_id}


# ── 模型管理 ───────────────────────────────────


@router.get("/models")
async def list_models() -> list[dict]:
    """列出已训练的模型。"""
    await run_blocking(_MODELS_DIR.mkdir, parents=True, exist_ok=True)
    models: list[dict] = []
    for f in sorted(_MODELS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            models.append(data)
        except Exception:
            continue
    return models


# ── 辅助函数 ───────────────────────────────────


def _load_jobs() -> list[dict]:
    """加载所有训练任务。"""
    path = _LEARNING_DIR / "jobs.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    """保存训练任务列表。"""
    _LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    path = _LEARNING_DIR / "jobs.json"
    path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


# 注：_check_gpu 已改为模块顶部的 async 缓存探针（见 _gpu_available）。
