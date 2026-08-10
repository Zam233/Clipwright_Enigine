"""B6 + E2: 规划书反馈闭环 — plan_ready 非确认 → 按反馈修订规划书并复用 raw_scenes。

修复前：plan_ready 收到非确认 → 回 gathering → 只重生成简报（反馈断裂）。
修复后：plan_ready 收到反馈 → 提取反馈 → 复用 raw_scenes 仅重新翻译 → 保持 plan_ready。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.services.requirements_service import RequirementsService


def _make_service() -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = AsyncMock()
    svc._cleanup_started = True
    return svc


def _seed_plan_session(svc: RequirementsService, session_id: str, plan: dict | None = None) -> None:
    from clipwright.services.requirements_service import _memory_sessions
    _memory_sessions[session_id] = {
        "session_id": session_id,
        "status": "plan_ready",
        "messages": [
            {
                "role": "assistant", "content": "规划书已生成",
                "timestamp": "2026-08-10T00:00:00+08:00",
                "metadata": {"plan_ready": True},
            },
        ],
        "user_inputs": {"topic": "测试主题", "persona_id": "default", "category_plugin_id": "knowledge_longform"},
        "creative_brief": {"title": "测试主题", "animation_style": {}},
        "production_plan": plan or {
            "markdown_content": "OLD PLAN",
            "scene_count": 2,
            "total_duration_sec": 120,
            "raw_scenes": [{"title": "s1", "duration_sec": 60.0}, {"title": "s2", "duration_sec": 60.0}],
        },
    }


class TestPlanFeedback:
    async def test_feedback_revises_plan_and_stays_plan_ready(self) -> None:
        """plan_ready + 非确认反馈 → 返回新 plan 且 status=plan_ready。"""
        svc = _make_service()
        session_id = "req_fb_1"
        _seed_plan_session(svc, session_id)

        async def _fake_generate_plan(brief, user_inputs, session_id, feedback="", existing_raw_scenes=None):
            return {
                "markdown_content": "NEW PLAN with 数据图表",
                "scene_count": 2,
                "total_duration_sec": 120,
                "raw_scenes": existing_raw_scenes,
            }

        with patch.object(svc, "_generate_plan", side_effect=_fake_generate_plan):
            result = await svc.chat(session_id, "增加数据图表")

        assert result.get("status") == "plan_ready"
        assert result.get("production_plan", {}).get("markdown_content") == "NEW PLAN with 数据图表"
        # 最后一条 assistant 消息注明已按反馈修订
        msgs = result.get("messages", [])
        last_assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        assert "修订" in last_assistant["content"]

    async def test_feedback_passes_existing_raw_scenes(self) -> None:
        """修订路径把现有 raw_scenes 传给 _generate_plan 供复用（E2）。"""
        svc = _make_service()
        session_id = "req_fb_2"
        plan = {
            "markdown_content": "OLD",
            "scene_count": 1,
            "total_duration_sec": 60,
            "raw_scenes": [{"title": "s1", "duration_sec": 60.0}],
        }
        _seed_plan_session(svc, session_id, plan)

        captured: dict = {}

        async def _fake_generate_plan(brief, user_inputs, session_id, feedback="", existing_raw_scenes=None):
            captured["feedback"] = feedback
            captured["existing_raw_scenes"] = existing_raw_scenes
            return {
                "markdown_content": "REVISED",
                "scene_count": 1,
                "total_duration_sec": 60,
                "raw_scenes": existing_raw_scenes,
            }

        with patch.object(svc, "_generate_plan", side_effect=_fake_generate_plan):
            await svc.chat(session_id, "请调整结构")

        assert captured["existing_raw_scenes"] == plan["raw_scenes"]
        assert "请调整结构" in captured["feedback"]

    async def test_confirm_still_works_after_plan_ready(self) -> None:
        """plan_ready + 确认 → 仍进入 plan_confirmed（不回退）。"""
        svc = _make_service()
        session_id = "req_fb_3"
        _seed_plan_session(svc, session_id)

        with patch.object(svc, "_generate_plan", new=AsyncMock()):
            result = await svc.chat(session_id, "确认实施")

        assert result.get("status") == "plan_confirmed"

    async def test_generation_failure_keeps_old_plan(self) -> None:
        """修订失败（_generate_plan 返回 None）→ 保留旧规划书 + 提示。"""
        svc = _make_service()
        session_id = "req_fb_4"
        _seed_plan_session(svc, session_id)

        with patch.object(svc, "_generate_plan", new=AsyncMock(return_value=None)):
            result = await svc.chat(session_id, "修改")

        assert result.get("status") == "plan_ready"
        # 旧规划书仍在
        assert result.get("production_plan", {}).get("markdown_content") == "OLD PLAN"
        msgs = result.get("messages", [])
        last_assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        assert "保留" in last_assistant["content"] or "未能" in last_assistant["content"]


class TestRawScenesReuse:
    async def test_generate_plan_reuses_existing_scenes_without_structure_agent(self) -> None:
        """existing_raw_scenes 非空 → 不调用 StructureAgent，直接 _translate_plan。"""
        svc = _make_service()
        session_id = "req_fb_5"
        scenes = [{"title": "s1", "duration_sec": 60.0}]

        async def _fake_translate(scenes_arg, brief, script_text="", feedback="", web_context=""):
            return {"markdown_content": "REUSED", "scene_count": len(scenes_arg), "total_duration_sec": 60, "raw_scenes": scenes_arg}

        with (
            patch("clipwright.agents.structure_agent.StructureAgent") as mock_structure,
            patch.object(svc, "_translate_plan", side_effect=_fake_translate) as mock_translate,
        ):
            result = await svc._generate_plan(
                {"title": "t"}, {"topic": "t", "persona_id": "default", "category_plugin_id": "knowledge_longform"},
                session_id, feedback="改一下", existing_raw_scenes=scenes,
            )

        mock_structure.assert_not_called()
        mock_translate.assert_awaited_once()
        assert result["markdown_content"] == "REUSED"
