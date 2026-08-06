"""422 请求校验错误中文化测试（U7a）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import _friendly_validation_message, app


def test_int_parsing_error_returns_chinese_detail() -> None:
    """数字参数收到字符串时，422 detail 应为中文并包含参数名。"""
    client = TestClient(app)
    resp = client.get("/api/pipeline/runs", params={"limit": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    assert "需要是数字" in body["detail"]
    assert "limit" in body["detail"]
    # 原始错误列表保留在 errors 字段
    assert isinstance(body["errors"], list)
    assert body["errors"]
    assert body["errors"][0]["type"] == "int_parsing"


def test_friendly_message_missing_param() -> None:
    msg = _friendly_validation_message(
        [{"type": "missing", "loc": ("body", "topic"), "msg": "Field required"}]
    )
    assert msg == "缺少必填参数：topic"


def test_friendly_message_string_type() -> None:
    msg = _friendly_validation_message(
        [{"type": "string_type", "loc": ("query", "name"), "msg": "Input should be a string"}]
    )
    assert "name" in msg
    assert "格式不正确" in msg


def test_friendly_message_default_fallback() -> None:
    msg = _friendly_validation_message(
        [{"type": "value_error", "loc": ("body", "config", "fps"), "msg": "bad value"}]
    )
    assert "config.fps" in msg
    assert "bad value" in msg


def test_friendly_message_empty_errors() -> None:
    # 无错误详情时不应抛异常
    msg = _friendly_validation_message([])
    assert isinstance(msg, str) and msg
