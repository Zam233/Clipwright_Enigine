"""P5-B7/B4: 多方案择优与渲染队列恢复的单元测试（无 Mongo 依赖）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.agents.structure_agent import _pick_better_scenes
from clipwright.main import app


class TestPickBetterScenes:
    def test_prefers_more_valid_scenes(self):
        a = ([{"title": "a", "description": "x"}], [])
        b = ([{"title": "a", "description": "x"}, {"title": "b", "description": "y"}], [])
        picked = _pick_better_scenes(a, b)
        assert len(picked[0]) == 2

    def test_penalizes_out_of_range(self):
        # a=0 场景 → 惩罚 -1；b=21 场景 → 惩罚 -13 → 选 a
        a = ([], [])
        b = ([{"title": f"s{i}"} for i in range(21)], [])
        picked = _pick_better_scenes(a, b)
        assert picked[0] == []

    def test_tie_keeps_first(self):
        a = ([{"title": "x", "description": "d"}], ["w1"])
        b = ([{"title": "y", "description": "e"}], ["w2"])
        picked = _pick_better_scenes(a, b)
        assert picked is a


class TestRenderQueueRecovery:
    def test_list_queue_marks_recovered_tasks(self, monkeypatch):
        from clipwright.api import render as render_api

        monkeypatch.setattr(
            render_api, "_load_recovered_render_tasks",
            lambda: [{"task_id": "render_old", "status": "rendering",
                      "progress": 40, "owner_id": "", "recovered": True}],
        )
        client = TestClient(app)
        resp = client.get("/api/render/queue")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        recovered = [t for t in tasks if t.get("recovered")]
        assert len(recovered) == 1
        assert recovered[0]["task_id"] == "render_old"
