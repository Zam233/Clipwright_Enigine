"""Webhook API — 外部回调注册与事件通知。

支持注册 Webhook URL，当管线完成、渲染完成等事件发生时，
自动向注册的 URL 发送 POST 通知。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.config import TIME_ZONE, logger

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

# Webhook 配置持久化文件
_WEBHOOKS_FILE = Path("webhooks.json")

# 支持的事件类型
SUPPORTED_EVENTS = [
    "pipeline.completed",
    "pipeline.failed",
    "render.completed",
    "render.failed",
    "asset.imported",
    "preprocess.completed",
]


# ── 请求/响应模型 ──────────────────────────────


class WebhookConfig(BaseModel):
    """Webhook 配置。"""
    webhook_id: str = ""
    url: str = Field(description="回调 URL")
    events: list[str] = Field(description="订阅的事件列表")
    secret: str = Field(default="", description="签名密钥 (可选)")
    active: bool = Field(default=True)
    created_at: str = ""
    description: str = ""


class RegisterWebhookRequest(BaseModel):
    """注册 Webhook 请求。"""
    url: str = Field(description="回调 URL (https://...)")
    events: list[str] = Field(description="订阅事件列表")
    secret: str = Field(default="", description="签名密钥")
    description: str = Field(default="")


class WebhookDelivery(BaseModel):
    """Webhook 投递记录。"""
    delivery_id: str
    webhook_id: str
    event: str
    status: str
    response_code: int = 0
    error: str = ""
    timestamp: str = ""


# ── 内部存储 ───────────────────────────────────

_webhooks: list[dict[str, Any]] = []
_delivery_log: list[dict[str, Any]] = []


def _load_webhooks() -> None:
    global _webhooks
    if _WEBHOOKS_FILE.exists():
        try:
            _webhooks = json.loads(_WEBHOOKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _webhooks = []


def _save_webhooks() -> None:
    _WEBHOOKS_FILE.write_text(
        json.dumps(_webhooks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_load_webhooks()


# ── API 端点 ───────────────────────────────────


@router.get("/events")
async def list_supported_events() -> dict:
    """列出所有支持的事件类型。"""
    return {"events": SUPPORTED_EVENTS}


@router.get("/list", response_model=list[WebhookConfig])
async def list_webhooks() -> list[WebhookConfig]:
    """列出所有已注册的 Webhook。"""
    return [WebhookConfig(**w) for w in _webhooks]


@router.post("/register", response_model=WebhookConfig)
async def register_webhook(req: RegisterWebhookRequest) -> WebhookConfig:
    """注册新的 Webhook。"""
    invalid = [e for e in req.events if e not in SUPPORTED_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported events: {invalid}. Supported: {SUPPORTED_EVENTS}",
        )

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    webhook = {
        "webhook_id": f"wh_{uuid.uuid4().hex[:10]}",
        "url": req.url,
        "events": req.events,
        "secret": req.secret,
        "active": True,
        "created_at": datetime.now(tz=TIME_ZONE).isoformat(),
        "description": req.description,
    }
    _webhooks.append(webhook)
    _save_webhooks()

    logger.info("Webhook 已注册: %s → %s (%s)", webhook["webhook_id"], req.url, req.events)
    return WebhookConfig(**webhook)


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str) -> dict:
    """删除 Webhook。"""
    global _webhooks
    before = len(_webhooks)
    _webhooks = [w for w in _webhooks if w["webhook_id"] != webhook_id]
    if len(_webhooks) == before:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")
    _save_webhooks()
    return {"status": "deleted", "webhook_id": webhook_id}


@router.put("/{webhook_id}/toggle")
async def toggle_webhook(webhook_id: str) -> dict:
    """启用/禁用 Webhook。"""
    for w in _webhooks:
        if w["webhook_id"] == webhook_id:
            w["active"] = not w["active"]
            _save_webhooks()
            return {"webhook_id": webhook_id, "active": w["active"]}
    raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")


@router.post("/{webhook_id}/test")
async def test_webhook(webhook_id: str) -> dict:
    """发送测试事件到指定 Webhook。"""
    webhook = next((w for w in _webhooks if w["webhook_id"] == webhook_id), None)
    if webhook is None:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")

    payload = {
        "event": "webhook.test",
        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
        "data": {"message": "This is a test event from ClipWright"},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook["url"], json=payload)
            return {
                "status": "sent",
                "response_code": resp.status_code,
                "response_body": resp.text[:500],
            }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.post("/notify")
async def notify(payload: dict) -> dict:
    """接收外部 webhook 通知（被动接收）。"""
    logger.info("收到外部 Webhook 通知: %s", list(payload.keys()))
    return {"status": "received"}


@router.get("/deliveries", response_model=list[WebhookDelivery])
async def list_deliveries(limit: int = 50) -> list[WebhookDelivery]:
    """查看最近的 Webhook 投递记录。"""
    return [WebhookDelivery(**d) for d in _delivery_log[-limit:]]


# ── 内部接口：供其他模块调用 ──────────────────


async def dispatch_event(event: str, data: dict[str, Any]) -> int:
    """分发事件到所有匹配的 Webhook。返回成功投递数。"""
    targets = [w for w in _webhooks if w["active"] and event in w["events"]]
    if not targets:
        return 0

    payload = {
        "event": event,
        "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
        "data": data,
    }

    success_count = 0
    for webhook in targets:
        delivery: dict[str, Any] = {
            "delivery_id": f"dl_{uuid.uuid4().hex[:8]}",
            "webhook_id": webhook["webhook_id"],
            "event": event,
            "status": "pending",
            "response_code": 0,
            "error": "",
            "timestamp": datetime.now(tz=TIME_ZONE).isoformat(),
        }
        try:
            headers: dict[str, str] = {}
            if webhook.get("secret"):
                body = json.dumps(payload, ensure_ascii=False)
                sig = hmac.new(
                    webhook["secret"].encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-ClipWright-Signature"] = f"sha256={sig}"

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(webhook["url"], json=payload, headers=headers)
                delivery["response_code"] = resp.status_code
                delivery["status"] = "success" if resp.status_code < 400 else "failed"
                if resp.status_code >= 400:
                    delivery["error"] = f"HTTP {resp.status_code}"
                else:
                    success_count += 1
        except Exception as e:
            delivery["status"] = "failed"
            delivery["error"] = str(e)[:200]

        _delivery_log.append(delivery)
        if len(_delivery_log) > 200:
            _delivery_log[:] = _delivery_log[-200:]

    logger.info("Webhook 事件分发: %s → %d/%d 成功", event, success_count, len(targets))
    return success_count
