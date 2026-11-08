"""M5c（PRD §15.5）：需求会话的 ask_user 闭环。

覆盖：
1. LLM 结构化输出带 `ask_user` → 建问题、会话进 `waiting_user`、question 事件落会话流；
2. 回答 → agent 继续（chat 被再次调用，答案以用户消息形式喂回）；
3. 刷新/重连：会话事件流按 seq 可补齐，question/answer 都在；
4. 归属与错误码：跨会话答别人的问题 404、已答 409、失效 410；
5. 需求会话不碰任务事件 API（事件键是 `requirements:<session_id>`）。
"""

from __future__ import annotations

import pytest

from clipwright.services import ask_user
from clipwright.services.requirements_service import (
    REQUIREMENTS_OWNER,
    RequirementsService,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """每个用例独立的工作区与问答库（避免 id 共享串味）。"""
    ask_user.reset()
    monkeypatch.setattr(ask_user, "_store_path", lambda *a, **k: tmp_path / "ask.json")
    yield
    ask_user.reset()


def _svc(session: dict) -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._cleanup_started = True
    state = {"session": dict(session)}
    svc.get_session = lambda sid: dict(state["session"])  # type: ignore[method-assign]

    def _persist(session_id, status, messages, brief, plan, inputs):  # noqa: ANN001
        state["session"].update(
            {"status": status, "messages": list(messages),
             "creative_brief": brief, "production_plan": plan,
             "user_inputs": inputs},
        )

    svc._persist = _persist  # type: ignore[method-assign]
    svc._state = state  # type: ignore[attr-defined]
    return svc


class TestAskFromLLM:
    async def test_llm_directive_creates_question_and_waits(self) -> None:
        svc = _svc({"status": "gathering", "messages": [], "user_inputs": {}})

        async def fake_gathering(*a, **k):  # noqa: ANN002, ANN003
            return {
                "reply": "我需要你定一下风格",
                "brief_draft": {},
                "is_ready": False,
                "ask_user": {
                    "question": "成片风格要哪一种？",
                    "message": "先说清风格，我才好定分镜",
                    "options": [
                        {"id": "a", "label": "冷静科技"},
                        {"id": "b", "label": "活泼综艺"},
                    ],
                    "allow_free_text": True,
                },
            }

        svc._handle_gathering = fake_gathering  # type: ignore[method-assign]

        result = await svc.chat("sess_q1", "做一期数码评测")
        assert result["status"] == "waiting_user"
        q = result["question"]
        assert q["question"] == "成片风格要哪一种？"
        assert q["owner_type"] == REQUIREMENTS_OWNER
        assert q["owner_id"] == "sess_q1"
        assert q["message"] == "先说清风格，我才好定分镜"
        assert [o["id"] for o in q["options"]] == ["a", "b"]
        # 落库：pending 可查
        assert [x["question_id"] for x in svc.pending_questions("sess_q1")] == [q["question_id"]]

    async def test_invalid_directive_does_not_block_the_turn(self) -> None:
        svc = _svc({"status": "gathering", "messages": [], "user_inputs": {}})

        async def fake_gathering(*a, **k):  # noqa: ANN002, ANN003
            return {"reply": "继续说说你的想法", "brief_draft": {}, "is_ready": False,
                    "ask_user": {"question": "   "}}

        svc._handle_gathering = fake_gathering  # type: ignore[method-assign]
        result = await svc.chat("sess_q2", "随便说说")
        assert result.get("status") != "waiting_user"
        assert svc.pending_questions("sess_q2") == []

    async def test_question_event_goes_to_session_stream(self) -> None:
        svc = _svc({"status": "gathering", "messages": [], "user_inputs": {}})

        async def fake_gathering(*a, **k):  # noqa: ANN002, ANN003
            return {"reply": "问一下", "brief_draft": {}, "is_ready": False,
                    "ask_user": {"question": "要不要配旁白？", "allow_free_text": True}}

        svc._handle_gathering = fake_gathering  # type: ignore[method-assign]
        await svc.chat("sess_ev", "开始")

        from clipwright.services import task_events

        stream_id = ask_user.session_stream_id("sess_ev")
        events = task_events.events(stream_id)
        assert [e.type for e in events] == ["question"]
        assert events[0].detail["question"] == "要不要配旁白？"
        # 没有污染任务流（键前缀隔离）
        assert task_events.events("sess_ev") == []


class TestAnswerContinues:
    async def test_answer_resumes_agent_with_chosen_option(self) -> None:
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question(
            "sess_a1", question="成片风格要哪一种？",
            options=[{"id": "a", "label": "冷静科技"}, {"id": "b", "label": "活泼综艺"}],
        )
        calls: list[str] = []

        async def fake_chat(session_id, message, **k):  # noqa: ANN001, ANN003
            calls.append(message)
            return {"status": "gathering", "reply": "好，按冷静科技推进"}

        svc.chat = fake_chat  # type: ignore[method-assign]
        out = await svc.answer_question("sess_a1", question_id=q["question_id"], option_id="a")
        assert out["answer"]["option_id"] == "a"
        assert out["reply"] == "好，按冷静科技推进"
        # agent 继续：答案作为用户消息喂回，且是人话（选项 label）
        assert calls and "冷静科技" in calls[0]
        assert "成片风格要哪一种？" in calls[0]

    async def test_answer_records_answer_event(self) -> None:
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question("sess_a2", question="要不要配旁白？", allow_free_text=True)
        svc.chat = lambda *a, **k: _coro({"status": "gathering", "reply": "ok"})  # type: ignore[method-assign]
        await svc.answer_question("sess_a2", question_id=q["question_id"], text="要，女声")

        from clipwright.services import task_events

        events = task_events.events(ask_user.session_stream_id("sess_a2"))
        assert [e.type for e in events] == ["question", "answer"]
        assert events[1].detail["text"] == "要，女声"

    async def test_cross_session_answer_is_404(self) -> None:
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question("owner_sess", question="风格？", allow_free_text=True)
        out = await svc.answer_question("other_sess", question_id=q["question_id"], text="x")
        assert out["error"] == "question_not_found"
        assert out["http_status"] == 404

    async def test_double_answer_is_409(self) -> None:
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question("sess_a3", question="风格？", allow_free_text=True)
        svc.chat = lambda *a, **k: _coro({"status": "gathering", "reply": "ok"})  # type: ignore[method-assign]
        await svc.answer_question("sess_a3", question_id=q["question_id"], text="A")
        out = await svc.answer_question("sess_a3", question_id=q["question_id"], text="B")
        assert out["error"] == "already_answered"
        assert out["http_status"] == 409

    async def test_expired_question_is_410(self) -> None:
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question("sess_a4", question="风格？", allow_free_text=True)
        ask_user.expire_owner(REQUIREMENTS_OWNER, "sess_a4")
        out = await svc.answer_question("sess_a4", question_id=q["question_id"], text="A")
        assert out["error"] == "question_expired"
        assert out["http_status"] == 410


async def _coro(value):  # noqa: ANN001, ANN202
    return value


class TestTypedMessageAsAnswer:
    async def test_typing_while_waiting_counts_as_answer(self) -> None:
        """用户没用问题卡、直接打字 → 当成自由文本回答，会话继续（不留死胡同）。"""
        svc = _svc({"status": "waiting_user", "messages": [], "user_inputs": {}})
        q = svc.ask_user_question("sess_t1", question="风格？", allow_free_text=True)

        async def fake_gathering(*a, **k):  # noqa: ANN002, ANN003
            return {"reply": "收到，按你说的走", "brief_draft": {"title": "T"}, "is_ready": True}

        svc._handle_gathering = fake_gathering  # type: ignore[method-assign]
        result = await svc.chat("sess_t1", "就冷静科技风")
        rec = ask_user.get(q["question_id"])
        assert rec is not None and rec["status"] == "answered"
        assert rec["answer"]["text"] == "就冷静科技风"
        assert result["status"] == "brief_ready"
