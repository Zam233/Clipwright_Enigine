"""A-18：legacy plan 反向生成（老会话/翻译失败时的兜底）。"""

from __future__ import annotations

from clipwright.services.requirements_service import RequirementsService


def _service() -> RequirementsService:
    return RequirementsService()


class TestLegacyPlan:
    def test_generates_from_scenes(self) -> None:
        service = _service()
        payload = service._legacy_plan_from_scenes(
            {"raw_scenes": [
                {"id": "s1", "title": "开场", "duration_sec": 5.0, "description": "介绍"},
                {"id": "s2", "title": "正片", "duration_sec": 15.0},
            ]},
            "session-1",
        )
        assert payload is not None
        assert payload["legacy"] is True
        assert payload["scene_count"] == 2
        assert payload["total_duration_sec"] == 20.0
        assert payload["plan"]["legacy"] is True
        assert "开场" in payload["markdown_content"]

    def test_no_scenes_returns_none(self) -> None:
        service = _service()
        assert service._legacy_plan_from_scenes({}, "s") is None
        assert service._legacy_plan_from_scenes(None, "s") is None
        assert service._legacy_plan_from_scenes({"raw_scenes": []}, "s") is None

    def test_invalid_scenes_rejected(self) -> None:
        service = _service()
        bad = {"raw_scenes": [{"id": "a", "duration_sec": -5.0}]}
        assert service._legacy_plan_from_scenes(bad, "s") is None

    def test_keeps_existing_plan_fields(self) -> None:
        service = _service()
        payload = service._legacy_plan_from_scenes(
            {"raw_scenes": [{"id": "s1", "title": "t", "duration_sec": 3.0}],
             "markdown_content": "旧文本", "summary": "旧摘要"},
            "s",
        )
        assert payload is not None
        assert payload["legacy"] is True
        assert payload["markdown_content"] != "旧文本"  # 用确定性渲染覆盖
        assert payload["scene_count"] == 1
