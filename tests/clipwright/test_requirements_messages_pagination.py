"""M4（unified-stream PRD §M4）：需求消息历史分页 + 会话事件向前游标。

三条硬约束：
1. 消息 id 必须**稳定**——否则前端每次刷新/翻页都重新 uid，翻页必重复；
2. `before_id` 不含边界（拿上一页最早那条的 id 再取，不重复）；
3. 事件 `before_seq` 只给更早的一段（往前翻），`since_seq` 只给更新的一段（补拉），
   两者不能互相污染。
"""

from __future__ import annotations

import pytest

from clipwright.services import ask_user
from clipwright.services.requirements_service import RequirementsService


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    ask_user.reset()
    monkeypatch.setattr(ask_user, "_store_path", lambda *a, **k: tmp_path / "ask.json")
    from clipwright.services import task_events

    task_events._STREAMS.clear()
    yield
    ask_user.reset()


def _svc(messages: list[dict]) -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._cleanup_started = True            # type: ignore[attr-defined]
    svc.get_session = lambda sid: {"messages": messages}  # type: ignore[method-assign]
    return svc


def _msgs(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"第 {i} 条", "timestamp": f"t{i}"} for i in range(n)]


class TestStableIds:
    def test_missing_ids_are_filled_once_and_stable(self) -> None:
        msgs = _msgs(4)
        once = RequirementsService._ensure_message_ids(msgs)
        twice = RequirementsService._ensure_message_ids(once)
        assert [m["id"] for m in once] == [m["id"] for m in twice]
        assert len({m["id"] for m in once}) == 4

    def test_existing_ids_are_kept(self) -> None:
        msgs = [{"id": "keep_me", "role": "user", "content": "hi"}]
        assert RequirementsService._ensure_message_ids(msgs)[0]["id"] == "keep_me"


class TestMessagePagination:
    def test_latest_page_when_no_cursor(self) -> None:
        svc = _svc(_msgs(120))
        page = svc.list_messages("s1", limit=50)
        assert len(page["items"]) == 50
        assert page["has_more"] is True
        assert page["total"] == 120
        # 最新一页是最末 50 条
        assert page["items"][-1]["content"] == "第 119 条"

    def test_before_id_excludes_boundary_and_walks_back(self) -> None:
        svc = _svc(_msgs(120))
        page1 = svc.list_messages("s1", limit=50)
        page2 = svc.list_messages("s1", before_id=page1["oldest_id"], limit=50)
        ids1 = {m["id"] for m in page1["items"]}
        ids2 = {m["id"] for m in page2["items"]}
        assert ids1.isdisjoint(ids2), "翻页不能重复"
        assert page2["has_more"] is True

        page3 = svc.list_messages("s1", before_id=page2["oldest_id"], limit=50)
        assert len(page3["items"]) == 20
        assert page3["has_more"] is False, "到头了必须说没有更多"

    def test_empty_session(self) -> None:
        svc = _svc([])
        page = svc.list_messages("s1")
        assert page["items"] == [] and page["has_more"] is False

    def test_limit_is_clamped(self) -> None:
        svc = _svc(_msgs(400))
        assert len(svc.list_messages("s1", limit=10_000)["items"]) == 200


class TestSessionEventsCursor:
    def test_before_seq_returns_older_slice_only(self) -> None:
        from clipwright.services import task_events
        from clipwright.api.requirements import session_events
        from fastapi.testclient import TestClient
        from clipwright.main import app

        stream_id = ask_user.session_stream_id("sx")
        for i in range(1, 11):
            task_events.emit(stream_id, "narration", f"第 {i} 条")

        with TestClient(app) as c:
            fwd = c.get("/api/requirements/sx/events?since_seq=7").json()
            assert [e["seq"] for e in fwd["items"]] == [8, 9, 10]

            back = c.get("/api/requirements/sx/events?before_seq=6&limit=3").json()
            assert [e["seq"] for e in back["items"]] == [3, 4, 5]
            assert back["has_more"] is True

            head = c.get("/api/requirements/sx/events?before_seq=3&limit=3").json()
            assert [e["seq"] for e in head["items"]] == [1, 2]
            assert head["has_more"] is False
        assert session_events is not None
