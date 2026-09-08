"""轮70（D8）：Webhook 投递重试 / 签名 / 所有权 / 非阻塞分发。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from clipwright.api import webhook as W
from clipwright.services.webhook_crypto import encrypt_secret


def _patch_http(monkeypatch, codes: list[int], calls: list[dict]) -> None:
    """伪造 httpx.AsyncClient：按 codes 顺序返回状态码（负数=抛网络异常）。"""

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            calls.append({"url": url, "body": content, "headers": headers or {}})
            code = codes.pop(0)
            if code < 0:
                raise RuntimeError("conn reset")
            return SimpleNamespace(status_code=code)

    monkeypatch.setattr(W.httpx, "AsyncClient", FakeClient)
    # 退避不真等待
    async def _no_sleep(_sec):
        return None

    monkeypatch.setattr(W.asyncio, "sleep", _no_sleep)


def _request(uid: str | None = None, role: str | None = None):
    return SimpleNamespace(state=SimpleNamespace(user_id=uid, user_role=role))


def _wh(webhook_id: str, owner: str | None, secret: str = "") -> dict:
    return {
        "webhook_id": webhook_id,
        "url": "https://example.com/hook",
        "events": ["render.completed"],
        "secret": encrypt_secret(secret) if secret else "",
        "active": True,
        "owner_id": owner,
        "created_at": "",
        "description": "",
    }


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_5xx_then_succeeds(self, monkeypatch) -> None:
        calls: list[dict] = []
        _patch_http(monkeypatch, [500, 503, 200], calls)
        code, err, attempts = await W._post_with_retry(
            "https://example.com/hook", "{}", {}
        )
        assert (code, err, attempts) == (200, "", 3)
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_network_error_retries_then_fails(self, monkeypatch) -> None:
        calls: list[dict] = []
        _patch_http(monkeypatch, [-1, -1, -1], calls)
        code, err, attempts = await W._post_with_retry(
            "https://example.com/hook", "{}", {}
        )
        assert attempts == 3
        assert code == 0 and "conn reset" in err

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self, monkeypatch) -> None:
        calls: list[dict] = []
        _patch_http(monkeypatch, [404], calls)
        code, err, attempts = await W._post_with_retry(
            "https://example.com/hook", "{}", {}
        )
        assert (code, err, attempts) == (404, "HTTP 404", 1)
        assert len(calls) == 1


class TestTestEventSignature:
    @pytest.mark.asyncio
    async def test_test_event_signed_like_real_delivery(self, monkeypatch) -> None:
        calls: list[dict] = []
        _patch_http(monkeypatch, [200], calls)
        monkeypatch.setattr(W, "_webhooks", [_wh("wh_sig", None, secret="s3cr3t")])
        monkeypatch.setattr(
            "clipwright.security.assert_public_url", lambda url: None
        )

        out = await W.test_webhook("wh_sig", _request())
        assert out["status"] == "sent"
        headers = calls[0]["headers"]
        body = calls[0]["body"]
        expected = hmac.new(b"s3cr3t", body.encode(), hashlib.sha256).hexdigest()
        assert headers["X-ClipWright-Signature"] == f"sha256={expected}"
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body)["event"] == "webhook.test"

    @pytest.mark.asyncio
    async def test_content_type_always_sent_without_secret(self, monkeypatch) -> None:
        """无 secret 时也必须带 Content-Type（旧实现 json= 自动设置，改 content= 后需显式）。"""
        calls: list[dict] = []
        _patch_http(monkeypatch, [200], calls)
        monkeypatch.setattr(W, "_webhooks", [_wh("wh_plain", None)])
        monkeypatch.setattr("clipwright.security.assert_public_url", lambda url: None)

        await W.test_webhook("wh_plain", _request())
        assert calls[0]["headers"].get("Content-Type") == "application/json"
        assert "X-ClipWright-Signature" not in calls[0]["headers"]


class TestOwnership:
    @pytest.mark.asyncio
    async def test_list_filters_by_owner(self, monkeypatch) -> None:
        monkeypatch.setattr(
            W, "_webhooks", [_wh("wh_a", "u1"), _wh("wh_b", "u2"), _wh("wh_legacy", None)]
        )
        out = await W.list_webhooks(_request("u1"))
        assert [w.webhook_id for w in out] == ["wh_a"]

    @pytest.mark.asyncio
    async def test_admin_sees_all(self, monkeypatch) -> None:
        monkeypatch.setattr(W, "_webhooks", [_wh("wh_a", "u1"), _wh("wh_b", "u2")])
        out = await W.list_webhooks(_request("u9", "admin"))
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_delete_other_owner_forbidden(self, monkeypatch) -> None:
        monkeypatch.setattr(W, "_webhooks", [_wh("wh_a", "u1"), _wh("wh_b", "u2")])
        with pytest.raises(HTTPException) as ei:
            await W.delete_webhook("wh_b", _request("u1"))
        assert ei.value.status_code == 403
        # 未被删除
        assert any(w["webhook_id"] == "wh_b" for w in W._webhooks)

    @pytest.mark.asyncio
    async def test_toggle_other_owner_forbidden(self, monkeypatch) -> None:
        monkeypatch.setattr(W, "_webhooks", [_wh("wh_b", "u2")])
        with pytest.raises(HTTPException) as ei:
            await W.toggle_webhook("wh_b", _request("u1"))
        assert ei.value.status_code == 403


class TestNonBlockingDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_event_bg_returns_immediately(self, monkeypatch) -> None:
        called = asyncio.Event()

        async def fake_dispatch(event, data):
            called.set()
            return 1

        monkeypatch.setattr(W, "dispatch_event", fake_dispatch)
        W.dispatch_event_bg("render.completed", {"task_id": "t1"})
        # 调用方未被阻塞（bg 立即返回），随后后台任务执行
        await asyncio.wait_for(called.wait(), timeout=1.0)
