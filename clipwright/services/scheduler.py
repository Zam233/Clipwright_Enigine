"""P8: 轻量定时调度器 — Mongo 持久化定时任务 + 后台循环触发。

- 任务存储在 clipwright.scheduled_runs（cron 简化版：间隔秒 or 每日固定时刻）；
- 后台 asyncio 循环每秒检查到期任务，触发回调（默认写 trace 事件 + 尝试启动管线）；
- 启动入口：main.py 调用 scheduler.start()；测试/CLI 可手动驱动 tick()。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from clipwright.config import logger

COLLECTION = "scheduled_runs"

# 全局单例状态（避免与 FastAPI lifespan 纠缠，惰性启动）
_task: Optional[asyncio.Task] = None
_running = False
_handler: Optional[Callable[[dict], Any]] = None


def _coll():
    from clipwright.context import mongo
    return mongo.db[COLLECTION]


def create_schedule(
    name: str,
    interval_sec: int = 0,
    daily_hhmm: str = "",
    payload: Optional[dict] = None,
    owner_id: str = "",
) -> dict:
    """创建定时任务。interval_sec > 0 为间隔触发；daily_hhmm（HH:MM）为每日时刻。"""
    if interval_sec <= 0 and not daily_hhmm:
        raise ValueError("必须提供 interval_sec 或 daily_hhmm 之一")
    doc = {
        "schedule_id": f"sched_{uuid.uuid4().hex[:10]}",
        "name": name,
        "interval_sec": max(0, int(interval_sec)),
        "daily_hhmm": daily_hhmm,
        "payload": payload or {},
        "owner_id": owner_id,
        "active": True,
        "last_run_at": None,
        "next_run_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _coll().insert_one(doc)
    logger.info("定时任务已创建: %s (%s)", name, doc["schedule_id"])
    return doc


def list_schedules(owner_id: str = "") -> list[dict]:
    query = {}
    if owner_id:
        query["owner_id"] = owner_id
    docs = list(_coll().find(query).sort("created_at", -1))
    for d in docs:
        d.pop("_id", None)
    return docs


def delete_schedule(schedule_id: str, owner_id: str = "") -> bool:
    query = {"schedule_id": schedule_id}
    if owner_id:
        query["owner_id"] = owner_id
    r = _coll().delete_one(query)
    return r.deleted_count > 0


def set_schedule_active(schedule_id: str, active: bool, owner_id: str = "") -> bool:
    query = {"schedule_id": schedule_id}
    if owner_id:
        query["owner_id"] = owner_id
    r = _coll().update_one(query, {"$set": {"active": active}})
    return r.modified_count > 0


def _due(doc: dict, now: datetime) -> bool:
    """判断任务是否到期。"""
    if not doc.get("active"):
        return False
    next_at = doc.get("next_run_at")
    if not next_at:
        return True
    try:
        nxt = datetime.fromisoformat(str(next_at))
        return now >= nxt
    except Exception:
        return False


def _compute_next(doc: dict, now: datetime) -> str:
    """计算下次执行时刻。"""
    from datetime import timedelta
    if doc.get("interval_sec", 0) > 0:
        nxt = now + timedelta(seconds=int(doc["interval_sec"]))
        return nxt.isoformat()
    hhmm = str(doc.get("daily_hhmm", "00:00"))
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
    except Exception:
        hh, mm = 0, 0
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def run_due_once(now: Optional[datetime] = None) -> list[dict]:
    """扫描并触发所有到期任务（同步；供后台循环与测试调用）。"""
    now = now or datetime.now(timezone.utc)
    fired: list[dict] = []
    try:
        due_docs = list(_coll().find())
    except Exception as e:
        logger.warning("scheduler 扫描失败（Mongo 未连接?）: %s", e)
        return fired
    for doc in due_docs:
        if not _due(doc, now):
            continue
        sid = doc.get("schedule_id", "")
        try:
            if _handler:
                try:
                    _handler({**doc.get("payload", {}), "schedule_id": sid, "name": doc.get("name", "")})
                except Exception as e:
                    logger.warning("定时任务回调失败 %s: %s", sid, e)
            _coll().update_one(
                {"schedule_id": sid},
                {"$set": {
                    "last_run_at": now.isoformat(),
                    "next_run_at": _compute_next(doc, now),
                }},
            )
            fired.append(doc)
            logger.info("定时任务触发: %s", doc.get("name", sid))
        except Exception as e:
            logger.warning("定时任务执行异常 %s: %s", sid, e)
    return fired


async def _loop(interval_sec: float = 1.0) -> None:
    global _running
    while _running:
        try:
            run_due_once()
        except Exception:
            pass
        await asyncio.sleep(interval_sec)


def start(handler: Optional[Callable[[dict], Any]] = None, interval_sec: float = 1.0) -> None:
    """启动后台调度循环（幂等）。"""
    global _task, _running, _handler
    if handler is not None:
        _handler = handler
    if _running:
        return
    _running = True
    _task = asyncio.get_event_loop().create_task(_loop(interval_sec))
    logger.info("定时调度器已启动")


def stop() -> None:
    global _task, _running
    _running = False
    if _task:
        _task.cancel()
        _task = None
