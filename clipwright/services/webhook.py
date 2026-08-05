"""Webhook 服务 — 管线/渲染完成通知。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from clipwright.config import logger

WEBHOOKS_FILE = Path("webhooks.json")

_subscribers: dict[str, list[dict[str, Any]]] = {"pipeline": [], "render": []}
_loaded = False


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    if WEBHOOKS_FILE.exists():
        try:
            data = json.loads(WEBHOOKS_FILE.read_text(encoding="utf-8"))
            _subscribers["pipeline"] = data.get("pipeline", [])
            _subscribers["render"] = data.get("render", [])
        except Exception as e:
            logger.warning("加载 webhook 配置失败: %s", e)


def _save():
    WEBHOOKS_FILE.write_text(
        json.dumps(_subscribers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def subscribe(event_type: str, url: str, secret: str = "") -> dict:
    """订阅事件通知。"""
    _load()
    entry = {"url": url, "secret": secret}
    _subscribers.setdefault(event_type, []).append(entry)
    _save()
    logger.info("Webhook 订阅 %s → %s", event_type, url)
    return {"status": "subscribed", "event_type": event_type, "url": url}


def unsubscribe(event_type: str, url: str) -> bool:
    """取消订阅。"""
    _load()
    before = len(_subscribers.get(event_type, []))
    _subscribers[event_type] = [e for e in _subscribers.get(event_type, []) if e["url"] != url]
    _save()
    return len(_subscribers[event_type]) < before


def list_subscriptions(event_type: str = "") -> dict[str, list[dict]]:
    """列出所有订阅。"""
    _load()
    if event_type:
        return {event_type: _subscribers.get(event_type, [])}
    return dict(_subscribers)


async def notify(event_type: str, payload: dict[str, Any]) -> list[str]:
    """向所有订阅者发送通知。"""
    _load()
    import httpx
    results = []
    for sub in _subscribers.get(event_type, []):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    sub["url"],
                    json={"event": event_type, "data": payload},
                    headers={"Content-Type": "application/json"},
                )
                results.append(f"{sub['url']}: {resp.status_code}")
        except Exception as e:
            logger.warning("Webhook 通知失败 %s: %s", sub["url"], e)
            results.append(f"{sub['url']}: error={e}")
    return results
