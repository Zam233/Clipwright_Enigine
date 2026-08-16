"""P8: 热点/选题发现测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.services import topic_discovery

client = TestClient(app)


def test_fallback_topics_shape() -> None:
    topics = topic_discovery._fallback_topics("", 3)
    assert len(topics) == 3
    for t in topics:
        assert "title" in t and "reason" in t


def test_suggest_endpoint_returns_topics(monkeypatch) -> None:
    async def fake(**_):
        return [{"title": "AI 选题", "reason": "热点"}]
    monkeypatch.setattr(topic_discovery, "suggest_topics", fake)
    resp = client.post("/api/pipeline/topic-suggest", json={"category": "科技", "count": 3})
    assert resp.status_code == 200
    assert len(resp.json()["topics"]) == 1
    assert resp.json()["topics"][0]["title"] == "AI 选题"
