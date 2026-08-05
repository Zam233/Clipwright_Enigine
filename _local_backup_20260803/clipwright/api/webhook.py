"""Webhook API — 订阅/管理事件通知。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from clipwright.services.webhook import subscribe, unsubscribe, list_subscriptions, notify

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.post("/subscribe")
async def subscribe_webhook(event_type: str = "pipeline", url: str = "", secret: str = "") -> dict:
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    return subscribe(event_type, url, secret)


@router.post("/unsubscribe")
async def unsubscribe_webhook(event_type: str = "pipeline", url: str = "") -> dict:
    ok = unsubscribe(event_type, url)
    if not ok:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "unsubscribed"}


@router.get("/subscriptions")
async def get_subscriptions(event_type: str = "") -> dict:
    return list_subscriptions(event_type)


@router.post("/test/{event_type}")
async def test_notification(event_type: str = "pipeline") -> dict:
    results = await notify(event_type, {"test": True, "message": "This is a test notification"})
    return {"event_type": event_type, "results": results}
