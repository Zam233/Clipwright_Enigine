"""B6（2026-10-28 PRD）：`/v1/models` 探测契约收口。

四态 + 错误码 + 「失败不清空已存模型」：
- ok：解析 OpenAI `data[].id` / Ollama `models[].name`；
- empty：200 但没有可用条目；
- unsupported：provider 没有列表实现；
- error：401（credential_error）/ 超时（timeout）/ 非 JSON（invalid_response）/ 5xx（http_error）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from clipwright.providers.model_listing import (
    LISTING_ERROR_CODES,
    LISTING_STATUSES,
    ModelListingResult,
    list_models_for_source,
    list_models_result,
)


class _Resp:
    def __init__(self, status_code: int = 200, payload: Any = None, *, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    """httpx.AsyncClient 替身：记录请求并回放预设响应/异常。"""

    def __init__(self, resp: _Resp | None = None, exc: Exception | None = None) -> None:
        self.resp = resp
        self.exc = exc
        self.requests: list[str] = []

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str) -> _Resp:
        self.requests.append(url)
        if self.exc is not None:
            raise self.exc
        assert self.resp is not None
        return self.resp


def _install(monkeypatch, *, resp: _Resp | None = None, exc: Exception | None = None) -> _Client:
    import httpx

    client = _Client(resp=resp, exc=exc)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    return client


def test_status_and_error_code_vocabulary():
    assert LISTING_STATUSES == ("ok", "empty", "unsupported", "error")
    assert "credential_error" in LISTING_ERROR_CODES and "timeout" in LISTING_ERROR_CODES
    assert ModelListingResult(status="ok", models=["m"]).ok is True
    assert ModelListingResult(status="empty").ok is False


def test_ok_parses_openai_data_ids(monkeypatch):
    _install(monkeypatch, resp=_Resp(200, {"data": [{"id": "gpt-4o"}, {"id": "o3"}]}))
    res = asyncio.run(list_models_result("openai", {"api_key": "k"}))
    assert res.status == "ok"
    assert res.models == ["gpt-4o", "o3"]
    assert res.note == ""


def test_ok_parses_ollama_models_names(monkeypatch):
    _install(monkeypatch, resp=_Resp(200, {"models": [{"name": "llama3:8b"}]}))
    res = asyncio.run(list_models_result("ollama", {}))
    assert res.status == "ok" and res.models == ["llama3:8b"]


def test_empty_when_payload_has_no_items(monkeypatch):
    _install(monkeypatch, resp=_Resp(200, {"data": []}))
    res = asyncio.run(list_models_result("openai", {}))
    assert res.status == "empty"
    assert res.error_code == "invalid_response"
    assert "手动添加" in res.note


def test_unsupported_provider(monkeypatch):
    client = _install(monkeypatch, resp=_Resp(200, {"data": [{"id": "x"}]}))
    res = asyncio.run(list_models_result("my-local-gateway", {}))
    assert res.status == "unsupported"
    assert res.error_code == "provider_unsupported"
    assert client.requests == [], "不支持的 provider 不该真的发请求"


def test_401_is_credential_error(monkeypatch):
    _install(monkeypatch, resp=_Resp(401, None))
    res = asyncio.run(list_models_result("openai", {"api_key": "bad"}))
    assert res.status == "error" and res.error_code == "credential_error"
    assert "API Key" in res.note


def test_500_is_http_error(monkeypatch):
    _install(monkeypatch, resp=_Resp(503, None))
    res = asyncio.run(list_models_result("openai", {}))
    assert res.status == "error" and res.error_code == "http_error"
    assert "503" in res.note


def test_timeout_is_distinguished(monkeypatch):
    _install(monkeypatch, exc=TimeoutError("timed out"))
    res = asyncio.run(list_models_result("openai", {}))
    assert res.status == "error" and res.error_code == "timeout"
    assert "超时" in res.note


def test_non_json_is_invalid_response(monkeypatch):
    _install(monkeypatch, resp=_Resp(200, None, text="<html>login</html>"))
    res = asyncio.run(list_models_result("openai", {}))
    assert res.status == "error" and res.error_code == "invalid_response"


def test_legacy_list_wrapper_still_works(monkeypatch):
    _install(monkeypatch, resp=_Resp(200, {"data": [{"id": "m1"}]}))
    assert asyncio.run(list_models_for_source("openai", {})) == ["m1"]
    _install(monkeypatch, resp=_Resp(401, None))
    assert asyncio.run(list_models_for_source("openai", {})) == []


# ── API 层：失败不清空已存模型 ────────────────────────────────


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from clipwright.api import providers as api_mod
    from clipwright.main import app
    from clipwright.services import provider_sources as ps

    ps.reset_for_tests(tmp_path / "provider_sources.json")
    monkeypatch.delenv("CLIPWRIGHT_ACCOUNT_VERIFY_MODE", raising=False)
    client = TestClient(app)
    src = ps.create_source({
        "name": "src-a", "kind": "llm", "provider": "openai",
        "base_url": "https://gw.example.com/v1", "api_keys": ["sk-1"],
        "models": [{"id": "kept-model", "enabled": True}],
    })
    yield client, str(src["id"]), ps, api_mod
    ps.reset_for_tests(None)


def test_api_ok_overwrites_models(api, monkeypatch):
    client, sid, ps, _ = api
    _install(monkeypatch, resp=_Resp(200, {"data": [{"id": "m-new"}]}))
    body = client.post(f"/api/providers/{sid}/models/fetch").json()
    assert body["status"] == "ok"
    assert body["source_id"] == sid and body["provider"] == "openai"
    assert [m["id"] for m in body["models"]] == ["m-new"]
    assert [m["id"] for m in ps.get_source(sid)["models"]] == ["m-new"]


def test_api_failure_keeps_existing_models(api, monkeypatch):
    client, sid, ps, _ = api
    _install(monkeypatch, resp=_Resp(401, None))
    body = client.post(f"/api/providers/{sid}/models/fetch").json()
    assert body["status"] == "error"
    assert body["error_code"] == "credential_error"
    assert body["note"]
    # 关键：失败**不清空**已存模型，用户的手动配置不能被一次失败抹掉
    assert [m["id"] for m in ps.get_source(sid)["models"]] == ["kept-model"]


def test_api_unsupported_keeps_existing_models(api, monkeypatch):
    client, sid, ps, _ = api
    ps.update_source(sid, {"provider": "weird-gateway"})
    body = client.post(f"/api/providers/{sid}/models/fetch").json()
    assert body["status"] == "unsupported"
    assert body["error_code"] == "provider_unsupported"
    assert [m["id"] for m in ps.get_source(sid)["models"]] == ["kept-model"]


def test_api_unknown_source_is_404(api):
    client, _, _, _ = api
    assert client.post("/api/providers/no_such/models/fetch").status_code == 404
