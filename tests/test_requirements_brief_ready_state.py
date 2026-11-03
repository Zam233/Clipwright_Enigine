"""E2E 2026-10-29 发现的会话状态机缺陷（brief_ready 误降级）。

现象（真实复现，K:\\demo 一镜到底）：
1. 会话在 `brief_ready`（方案已生成，等用户确认）；
2. 用户说「请直接给出制作规划书」——这不是确认，但旧实现把它当成"还没聊够"，
   于是 `status` 被改回 `gathering`、只回一句"请告诉我需要如何调整"；
3. 因为状态已不是 `brief_ready`，紧接着的「确认」落进 gathering 分支，
   又把方案重生成一遍——用户要说两次「确认」才推进，方案也被莫名覆盖。

修复后的契约：
- brief_ready 下的任何非确认消息 → **保留 brief_ready**，并把该消息当作修订意见
  重新合并方案（`_handle_gathering`）；
- 修订成功 → 再给一次确认提示；修订无变化 → 明说没变；
- 期间 brief 不被清空，用户随时可以「确认」推进。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services.requirements_service import RequirementsService


BRIEF = {
    "title": "从 PCL 到 HMCL：我们的 Minecraft 自由社区正在失去什么？",
    "overview": "梳理时间线与传闻变形，逐条排除解释，落到预防性合规与技术中立。",
    "target_audience": "MC 玩家 / 启动器用户 / 自由软件社区",
    "style_direction": "硬核理性批判",
    "duration_estimate": "10-12 分钟",
}


class _FakeSvc(RequirementsService):
    """最小替身：只测状态机，不碰 Mongo/LLM。"""

    def __init__(self, *, status: str, revised_brief: dict | None, confirm: bool):
        self._session = {
            "session_id": "s1",
            "status": status,
            "messages": [],
            "user_inputs": {"topic": "x"},
            "creative_brief": dict(BRIEF),
            "production_plan": None,
        }
        self._revised_brief = revised_brief
        self._confirm = confirm
        self.gathering_calls = 0

    async def _ensure_cleanup(self):  # noqa: D401, ANN001
        return None

    def get_session(self, session_id: str):  # noqa: ANN001
        return dict(self._session) if session_id == "s1" else None

    def _persist(self, session_id, status, messages, brief_data, plan_data,  # noqa: ANN001
                 user_inputs):  # noqa: ANN001
        """chat() 末尾会重新读一次会话，替身必须把落盘真的写进内存态。"""
        self._session.update({
            "status": status,
            "messages": list(messages or []),
            "creative_brief": brief_data,
            "production_plan": plan_data,
            "user_inputs": user_inputs,
        })
        return None

    async def _is_confirm(self, message: str) -> bool:  # noqa: ANN001
        return self._confirm

    async def _handle_gathering(self, messages, brief_data, status, user_inputs,
                                session_id, on_delta=None, on_reasoning=None):  # noqa: ANN001, ANN003
        self.gathering_calls += 1
        return {"brief_draft": self._revised_brief, "reply": "已按你的意见更新方案。",
                "is_ready": bool(self._revised_brief)}

    async def _generate_plan(self, brief_data, user_inputs, session_id):  # noqa: ANN001
        return {"scenes": [{"id": "s1"}], "scene_count": 1}

    async def _record_llm_usage(self, *a, **k):  # noqa: ANN002, ANN003
        return None

    async def _persist_session(self, *a, **k):  # noqa: ANN002, ANN003
        return None


def _make(*, revised_brief, confirm=False) -> _FakeSvc:
    return _FakeSvc(status="brief_ready", revised_brief=revised_brief, confirm=confirm)


@pytest.mark.asyncio
async def test_non_confirm_message_keeps_brief_ready():
    """「请直接给出制作规划书」这类非确认消息不得把会话打回 gathering。"""
    svc = _make(revised_brief=BRIEF)
    out = await svc.chat("s1", "简报可以了，请直接给出制作规划书。")

    assert out["status"] == "brief_ready", "方案已生成时不能退回 gathering"
    assert svc.gathering_calls == 1, "这条消息要真的被拿去修订方案，而不是白说一遍"
    texts = " ".join(str(m.get("content") or "") for m in out.get("messages") or [])
    assert "请告诉我需要如何调整" not in texts, "旧文案必须消失"
    assert "确认" in texts


@pytest.mark.asyncio
async def test_revision_reply_mentions_updated_brief():
    updated = dict(BRIEF, style_direction="更冷静，少形容词")
    svc = _make(revised_brief=updated)
    out = await svc.chat("s1", "风格再冷静一点")

    assert out["status"] == "brief_ready"
    assert out["creative_brief"]["style_direction"] == "更冷静，少形容词"
    texts = " ".join(str(m.get("content") or "") for m in out.get("messages") or [])
    assert "方案已按你的意见更新" in texts


@pytest.mark.asyncio
async def test_unchanged_revision_says_so():
    svc = _make(revised_brief=None)
    out = await svc.chat("s1", "嗯……")

    assert out["status"] == "brief_ready"
    texts = " ".join(str(m.get("content") or "") for m in out.get("messages") or [])
    assert "方案没有变动" in texts
    assert "方案已按你的意见更新" not in texts, "未产出新方案时不能谎称已更新"


@pytest.mark.asyncio
async def test_confirm_still_generates_plan():
    """对照组：确认路径不受影响（直接进 plan_ready）。"""
    svc = _make(revised_brief=BRIEF, confirm=True)
    out = await svc.chat("s1", "确认")

    assert out["status"] == "plan_ready"
    assert out["production_plan"], "确认后必须产生产规划书"
