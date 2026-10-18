"""V60：老会话（只有 raw_scenes）→ 反向生成 legacy plan 的会话分支单测。

真机脚本 `scripts/e2e_legacy_plan.py` 已经跑了整条链（含真管线与 proceed），
这里把**分支本身**钉进回归：seed 一个"老形态"会话 → 只把 `_generate_plan`
（LLM 翻译）替换成返回 None → 发「确认」→ 期望 `plan_ready` + `legacy: true`。

刻意不启动整个 app：lifespan 会重置注册表与运行记录，副作用太大
（第 29 轮踩过，污染了 test_pipeline_runs）。
"""

from __future__ import annotations

import pytest

from clipwright.services import requirements_service as rs
from clipwright.services.requirements_service import RequirementsService

OLD_SCENES = [
    {"title": "开场", "description": "版画台俯拍", "duration_sec": 5.0},
    {"title": "主体", "description": "套印特写", "duration_sec": 8.0},
    {"title": "收束", "description": "印张揭起", "duration_sec": 4.0},
]


def _seed(session_id: str, plan: dict | None) -> None:
    """直接种一份内存会话：真实数据形状，但不依赖 Mongo。"""
    rs._memory_sessions[session_id] = {
        "session_id": session_id,
        "status": "brief_ready",
        "messages": [],
        "user_inputs": {"topic": "三墨之间", "persona_id": "default"},
        "creative_brief": {"overview": "老简报", "core_message": "版画"},
        "production_plan": plan,
    }


@pytest.fixture(autouse=True)
def _no_mongo(monkeypatch):
    monkeypatch.setattr(rs, "_mongo_ok", lambda: False)
    yield


@pytest.fixture()
def old_session() -> tuple[RequirementsService, str]:
    service = RequirementsService()
    session_id = f"test_legacy_{id(service)}"
    _seed(session_id, {"raw_scenes": OLD_SCENES})
    yield service, session_id
    rs._memory_sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_confirm_generates_legacy_plan_and_moves_to_plan_ready(old_session, monkeypatch) -> None:
    service, session_id = old_session

    async def _fail(*_a, **_k):
        return None  # 模拟"规划书翻译失败"

    monkeypatch.setattr(service, "_generate_plan", _fail)
    result = await service.chat(session_id, "确认")

    assert result["status"] == "plan_ready"
    plan = result["production_plan"]
    assert plan["legacy"] is True
    assert plan["scene_count"] == len(OLD_SCENES)
    assert plan["markdown_content"].strip()
    assert len(plan["plan"]["sections"]) == len(OLD_SCENES)
    # 老数据不被清空——legacy 是"补出来"，不是"换掉"
    assert plan["raw_scenes"] == OLD_SCENES


@pytest.mark.asyncio
async def test_no_scenes_stays_brief_ready(monkeypatch) -> None:
    """真没有场景时不许硬造规划（明确回退而不是假装成功）。"""
    service = RequirementsService()
    session_id = f"test_legacy_empty_{id(service)}"
    _seed(session_id, {})

    async def _fail(*_a, **_k):
        return None

    monkeypatch.setattr(service, "_generate_plan", _fail)
    try:
        result = await service.chat(session_id, "确认")
        assert result["status"] == "brief_ready"
        assert not (result.get("production_plan") or {}).get("legacy")
    finally:
        rs._memory_sessions.pop(session_id, None)
