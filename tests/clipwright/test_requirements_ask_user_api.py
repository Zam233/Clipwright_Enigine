"""M5c（PRD §15.5）：需求会话问答接口——路由、错误码、事件流恢复。

服务层逻辑在 `test_requirements_ask_user.py`；这里只钉 HTTP 面：
- `GET /{session_id}/questions?status=pending|all`
- `POST /{session_id}/answer`（404 / 409 / 410 映射）
- `GET /{session_id}/events?since_seq=`（刷新/重连补齐，键独立于任务事件）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.services import ask_user
from clipwright.services.requirements_service import REQUIREMENTS_OWNER


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    ask_user.reset()
    monkeypatch.setattr(ask_user, "_store_path", lambda *a, **k: tmp_path / "ask.json")
    # 事件流是进程内单例：跨用例必须清空，否则 seq/条目会串味
    from clipwright.services import task_events

    task_events._STREAMS.clear()
    yield
    ask_user.reset()


@pytest.fixture()
def client(monkeypatch):
    """会话层打桩：这里只验问答路由，不跑真实需求对话。"""
    from clipwright.api import requirements as req_api

    async def _fake_answer_question(session_id, *, question_id, option_id=None, text=None):
        rec = ask_user.get(question_id)
        if not rec:
            return {"error": "question_not_found", "http_status": 410}
        if rec.get("owner_id") != session_id:
            return {"error": "question_not_found", "http_status": 404}
        try:
            ans = ask_user.answer(question_id, option_id=option_id, text=text)
        except ask_user.AskUserError as exc:
            return {"error": exc.code, "http_status": exc.http_status, **exc.detail}
        return {"answer": ans, "status": "gathering", "reply": "继续"}

    monkeypatch.setattr(req_api._service, "answer_question", _fake_answer_question)
    with TestClient(app) as c:
        yield c


def _ask(session_id: str = "sess_api", **kw) -> dict:
    kw.setdefault("question", "成片风格要哪一种？")
    kw.setdefault("options", [{"id": "a", "label": "冷静"}, {"id": "b", "label": "活泼"}])
    return ask_user.create(REQUIREMENTS_OWNER, session_id, **kw)


class TestQuestionsRoute:
    def test_pending_and_all(self, client: TestClient) -> None:
        q = _ask(options=[{"id": "a", "label": "冷静"}, {"id": "b", "label": "活泼"}])
        r = client.get("/api/requirements/sess_api/questions?status=pending")
        assert r.status_code == 200
        assert [x["question_id"] for x in r.json()["items"]] == [q["question_id"]]

        ask_user.answer(q["question_id"], option_id="a")
        assert client.get("/api/requirements/sess_api/questions?status=pending").json()["items"] == []
        allr = client.get("/api/requirements/sess_api/questions?status=all")
        assert [x["status"] for x in allr.json()["items"]] == ["answered"]


class TestAnswerRoute:
    def test_answer_ok(self, client: TestClient) -> None:
        q = _ask(allow_free_text=True)
        r = client.post("/api/requirements/sess_api/answer",
                        json={"question_id": q["question_id"], "text": "冷静科技风"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer"]["text"] == "冷静科技风"

    def test_unknown_question_410(self, client: TestClient) -> None:
        r = client.post("/api/requirements/sess_api/answer",
                        json={"question_id": "q_nope", "text": "x"})
        assert r.status_code == 410

    def test_double_answer_409(self, client: TestClient) -> None:
        q = _ask(allow_free_text=True)
        first = client.post("/api/requirements/sess_api/answer",
                            json={"question_id": q["question_id"], "text": "A"})
        assert first.status_code == 200
        second = client.post("/api/requirements/sess_api/answer",
                             json={"question_id": q["question_id"], "text": "B"})
        assert second.status_code == 409

    def test_empty_answer_400(self, client: TestClient) -> None:
        q = _ask()
        r = client.post("/api/requirements/sess_api/answer", json={"question_id": q["question_id"]})
        assert r.status_code == 400


class TestSessionEventsRoute:
    def test_question_and_answer_events(self, client: TestClient) -> None:
        q = _ask(allow_free_text=True)
        ask_user.answer(q["question_id"], text="要旁白")

        r = client.get("/api/requirements/sess_api/events?since_seq=0")
        assert r.status_code == 200, r.text
        body = r.json()
        assert [e["type"] for e in body["items"]] == ["question", "answer"]
        assert body["latest_seq"] == 2

        # 增量补齐：从 seq=1 起只剩 answer
        inc = client.get("/api/requirements/sess_api/events?since_seq=1").json()
        assert [e["type"] for e in inc["items"]] == ["answer"]

    def test_events_do_not_leak_task_stream(self, client: TestClient) -> None:
        from clipwright.services import task_events

        task_events.emit("sess_ev2", "narration", "任务侧的话")
        r = client.get("/api/requirements/sess_ev2/events?since_seq=0")
        assert r.status_code == 200
        assert r.json()["items"] == []
