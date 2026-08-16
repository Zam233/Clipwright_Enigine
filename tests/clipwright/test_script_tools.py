"""P8: 脚本创作工具（改写/扩写/缩写）测试 — LLM 失败回退启发式。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from clipwright.main import app

client = TestClient(app)


def test_script_tools_invalid_mode_400() -> None:
    resp = client.post("/api/pipeline/script-tools", json={"mode": "hack", "script_text": "x"})
    assert resp.status_code == 400


def test_script_tools_summarize_fallback() -> None:
    """LLM 不可用 → summarize 回退截断。"""
    with patch("clipwright.services.llm.LLMService.generate", new=AsyncMock(side_effect=RuntimeError("offline"))):
        resp = client.post("/api/pipeline/script-tools", json={
            "mode": "summarize", "script_text": "这是一段很长的文稿。", "max_length": 5,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "summarize"
    assert body["fallback"] is True
    assert len(body["result"]) <= 5


def test_script_tools_rewrite_success() -> None:
    """LLM 可用 → 返回处理结果。"""
    mock_resp = type("R", (), {"content": "改写后的文稿内容"})()
    with patch("clipwright.services.llm.LLMService.generate", new=AsyncMock(return_value=mock_resp)):
        resp = client.post("/api/pipeline/script-tools", json={
            "mode": "rewrite", "script_text": "原文", "style": "更口语化",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "rewrite"
    assert body["result"] == "改写后的文稿内容"
