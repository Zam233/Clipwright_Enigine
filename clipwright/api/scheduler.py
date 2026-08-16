"""P8: 定时调度 API — 定时任务 CRUD。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipwright.authz import current_user_id
from clipwright.config import logger
from clipwright.services import scheduler

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class CreateScheduleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    interval_sec: int = Field(default=0, ge=0, description="间隔秒（>0 时按间隔触发）")
    daily_hhmm: str = Field(default="", description="每日时刻 HH:MM")
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/schedules")
async def list_schedules(request: Request) -> list[dict]:
    """列出定时任务（jwt 模式按 owner 过滤）。"""
    uid = current_user_id(request)
    return scheduler.list_schedules(owner_id=uid or "")


@router.post("/schedules")
async def create_schedule(req: CreateScheduleRequest, request: Request) -> dict:
    """创建定时任务。"""
    uid = current_user_id(request)
    try:
        doc = scheduler.create_schedule(
            name=req.name,
            interval_sec=req.interval_sec,
            daily_hhmm=req.daily_hhmm,
            payload=req.payload,
            owner_id=uid or "",
        )
        return doc
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request) -> dict:
    """删除定时任务。"""
    uid = current_user_id(request)
    if not scheduler.delete_schedule(schedule_id, owner_id=uid or ""):
        raise HTTPException(status_code=404, detail=f"定时任务 {schedule_id} 不存在")
    return {"status": "deleted", "schedule_id": schedule_id}


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str, active: bool = True, request: Request = None) -> dict:
    """启用/停用定时任务。"""
    uid = current_user_id(request)
    if not scheduler.set_schedule_active(schedule_id, active, owner_id=uid or ""):
        raise HTTPException(status_code=404, detail=f"定时任务 {schedule_id} 不存在")
    return {"status": "ok", "schedule_id": schedule_id, "active": active}


@router.post("/tick")
async def tick_schedules(request: Request) -> dict:
    """手动触发一次到期任务扫描（运维/调试）。"""
    fired = scheduler.run_due_once()
    return {"fired": len(fired), "schedules": [s.get("schedule_id") for s in fired]}
