"""M5a：ask_user 后端契约测试（工具/事件/接口/幂等/超时/取消/重启）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipwright.services import ask_user


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    ask_user.reset()
    yield
    ask_user.reset()


# ── 1. 事件契约 ───────────────────────────────────────────────


def test_event_contract_covers_question_answer_progress_and_termination():
    """事件契约：原有 29 类 + 终止机制 2 类（goal_update / soft_landing）。

    2026-11-02 终止机制收口新增了两个事件；这里同时锁住"数量"与"关键成员"，
    并且要求没有重复项——只锁数量会在删一个加一个时静默通过。
    """
    from clipwright.schema.task import EVENT_TYPES

    assert len(EVENT_TYPES) == 31
    assert len(set(EVENT_TYPES)) == len(EVENT_TYPES), "事件类型不许重复"
    assert {"question", "answer", "progress"} <= set(EVENT_TYPES)
    assert {"goal_update", "soft_landing"} <= set(EVENT_TYPES)


def test_task_status_has_waiting_user():
    from clipwright.schema.task import TASK_TERMINAL, TaskStatus

    assert "waiting_user" in str(TaskStatus)
    assert "waiting_user" not in TASK_TERMINAL


# ── 2. 工具注册 ────────────────────────────────────────────────


def test_ask_user_registered_on_surface():
    from clipwright.tool import surface

    assert surface.ASK_USER in surface.AGENT_TOOLS
    assert surface._AGENT_TOOL_TIER[surface.ASK_USER] == "write_local"  # noqa: SLF001
    props = surface._AGENT_TOOL_SPECS[surface.ASK_USER]["parameters"]["properties"]  # noqa: SLF001
    assert {"question", "options", "allow_free_text", "default_option_id",
            "timeout_sec", "context_refs"} <= set(props)


# ── 3. create / answer 基础 ───────────────────────────────────


def _opts():
    return [{"id": "a", "label": "重派", "detail": "多花 3 分钟"},
            {"id": "b", "label": "保留", "detail": "动效偏弱"}]


def test_create_and_answer_once():
    q = ask_user.create("task", "task_a", question="选哪个？", options=_opts(),
                        allow_free_text=False, default_option_id="b", timeout_sec=60)
    assert q["status"] == "pending" and len(q["options"]) == 2

    ans = ask_user.answer(q["question_id"], option_id="a")
    assert ans["option_id"] == "a" and ans["source"] == "user_option"

    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.answer(q["question_id"], option_id="b")
    assert ei.value.code == "already_answered" and ei.value.http_status == 409


def test_create_requires_answerable_options():
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.create("task", "task_x", question="q", options=[{"id": "a", "label": "A"}],
                        allow_free_text=False, timeout_sec=60)
    assert ei.value.code == "not_answerable"


def test_free_text_only_when_allowed():
    q = ask_user.create("task", "task_ft", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.answer(q["question_id"], text="我自己写")
    assert ei.value.code == "free_text_not_allowed"

    q2 = ask_user.create("task", "task_ft2", question="q", options=_opts(),
                         allow_free_text=True, timeout_sec=60)
    ans = ask_user.answer(q2["question_id"], text="我自己写")
    assert ans["source"] == "user_text" and ans["text"] == "我自己写"


def test_invalid_option_and_default():
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.create("task", "task_d", question="q", options=_opts(),
                        allow_free_text=False, default_option_id="zzz", timeout_sec=60)
    assert ei.value.code == "invalid_default"

    q = ask_user.create("task", "task_d2", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    with pytest.raises(ask_user.AskUserError) as ei2:
        ask_user.answer(q["question_id"], option_id="zzz")
    assert ei2.value.code == "invalid_option"


def test_pending_limit_is_three():
    for i in range(3):
        ask_user.create("task", "task_lim", question=f"q{i}", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.create("task", "task_lim", question="q4", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    assert ei.value.code == "too_many_pending" and ei.value.http_status == 409


def test_pending_lists_only_unanswered():
    q1 = ask_user.create("task", "task_p", question="a", options=_opts(),
                         allow_free_text=False, timeout_sec=60)
    ask_user.create("task", "task_p", question="b", options=_opts(),
                    allow_free_text=False, timeout_sec=60)
    assert len(ask_user.pending("task", "task_p")) == 2
    ask_user.answer(q1["question_id"], option_id="a")
    assert len(ask_user.pending("task", "task_p")) == 1


# ── 4. 阻塞与超时 ─────────────────────────────────────────────


def test_ask_blocks_until_answered():
    async def _go():
        task = SimpleNamespace(id="task_blk", status="running", touch=lambda: None)
        call = asyncio.create_task(ask_user.ask(
            owner_type="task", owner_id="task_blk", question="选哪个？",
            options=_opts(), allow_free_text=False, timeout_sec=30, task=task))
        await asyncio.sleep(0.05)
        pend = ask_user.pending("task", "task_blk")
        assert len(pend) == 1 and task.status == "waiting_user"
        ask_user.answer(pend[0]["question_id"], option_id="b")
        out = await call
        assert out["status"] == "answered" and out["option_id"] == "b"
        assert task.status == "running", "回答后必须回到 running"

    asyncio.run(_go())


def test_timeout_uses_default_and_notes_degradation(monkeypatch):
    # 生产下限是 30s；这里为单测放开到 1s（只改校验常量，不改语义）
    monkeypatch.setattr(ask_user, "MIN_TIMEOUT_SEC", 1)
    notes: list[tuple] = []
    monkeypatch.setattr("clipwright.services.degradation.note_degradation",
                        lambda kind, **kw: notes.append((kind, kw)), raising=False)

    async def _go():
        task = SimpleNamespace(id="task_to", status="running", touch=lambda: None)
        out = await ask_user.ask(
            owner_type="task", owner_id="task_to", question="选哪个？",
            options=_opts(), allow_free_text=False, default_option_id="b",
            timeout_sec=1, task=task)
        assert out["timeout"] is True and out["option_id"] == "b"
        assert out["source"] == "timeout_default"

    asyncio.run(_go())
    assert any(k == "ask_user_timeout_default" for k, _ in notes)


def test_timeout_without_default_fails_honestly(monkeypatch):
    monkeypatch.setattr(ask_user, "MIN_TIMEOUT_SEC", 1)

    async def _go():
        task = SimpleNamespace(id="task_to2", status="running", touch=lambda: None)
        with pytest.raises(ask_user.AskUserError) as ei:
            await ask_user.ask(owner_type="task", owner_id="task_to2", question="q",
                               options=_opts(), allow_free_text=False, timeout_sec=1,
                               task=task)
        assert ei.value.code == "question_timeout"

    asyncio.run(_go())


# ── 5. 取消 / 重启 ────────────────────────────────────────────


def test_cancel_owner_voids_pending():
    q = ask_user.create("task", "task_c", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    assert ask_user.cancel_owner("task", "task_c") == 1
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.answer(q["question_id"], option_id="a")
    assert ei.value.code == "question_cancelled" and ei.value.http_status == 409


def test_restart_marks_pending_expired(tmp_path):
    """重启：磁盘里的 pending 一律 expired，answer 只能 410。"""
    q = ask_user.create("task", "task_r", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    qid = q["question_id"]
    # 模拟重启：清内存但保留磁盘
    ask_user.reset()
    assert ask_user.pending("task", "task_r") == []
    with pytest.raises(ask_user.AskUserError) as ei:
        ask_user.answer(qid, option_id="a")
    # 重启后：磁盘记录被读回时 pending 一律判 expired（答案无处投递）
    assert ei.value.http_status == 410
    assert ei.value.code in {"question_expired", "question_not_found"}


def test_persisted_file_records_question(tmp_path):
    q = ask_user.create("task", "task_file", question="落盘吗", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    path = ask_user._store_path("task", "task_file")  # noqa: SLF001
    assert path is not None and path.is_file()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["questions"][0]["question_id"] == q["question_id"]


# ── 6. REST 接口 ──────────────────────────────────────────────


class _FakeRequest:
    class _State:
        user_id = None
        user_role = None

    state = _State()
    headers: dict = {}

    def __getitem__(self, key):  # pragma: no cover
        raise KeyError(key)


def test_answer_endpoint_maps_errors(monkeypatch):
    from fastapi import HTTPException

    from clipwright.api.task import AnswerBody, answer_task_question

    q = ask_user.create("task", "task_api", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    body = AnswerBody(question_id=q["question_id"], option_id="a")
    out = asyncio.run(answer_task_question("task_api", body, _FakeRequest()))
    assert out["status"] == "accepted" and out["option_id"] == "a"

    with pytest.raises(HTTPException) as ei:
        asyncio.run(answer_task_question("task_api", body, _FakeRequest()))
    assert ei.value.status_code == 409
    assert ei.value.detail["reason"] == "already_answered"


def test_questions_endpoint_lists_pending():
    from clipwright.api.task import list_task_questions

    ask_user.create("task", "task_q", question="q1", options=_opts(),
                    allow_free_text=False, timeout_sec=60)
    out = asyncio.run(list_task_questions("task_q", _FakeRequest(), status="pending"))
    assert out["count"] == 1
    assert out["items"][0]["options"][0]["id"] == "a"


def test_resume_rejects_waiting_user_with_question(monkeypatch):
    from fastapi import HTTPException

    from clipwright.api import task as task_api

    q = ask_user.create("task", "task_rs", question="q", options=_opts(),
                        allow_free_text=False, timeout_sec=60)
    fake_task = SimpleNamespace(id="task_rs", status="waiting_user", terminal=False,
                                touch=lambda: None)
    monkeypatch.setattr(task_api, "_kernel",
                        lambda: SimpleNamespace(get=lambda tid: fake_task), raising=False)
    scheduled: list[str] = []
    monkeypatch.setattr(task_api, "_schedule", lambda tid: scheduled.append(tid),
                        raising=False)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(task_api.resume_task("task_rs", _FakeRequest()))
    assert ei.value.status_code == 409
    assert ei.value.detail["reason"] == "waiting_answer"
    assert ei.value.detail["question_id"] == q["question_id"]
    assert scheduled == [], "等待回答时不得重新调度"


def test_resume_allows_after_restart_downgrade(monkeypatch):
    from clipwright.api import task as task_api

    fake_task = SimpleNamespace(id="task_rs2", status="waiting_user", terminal=False,
                                touch=lambda: None)
    monkeypatch.setattr(task_api, "_kernel",
                        lambda: SimpleNamespace(get=lambda tid: fake_task), raising=False)
    scheduled: list[str] = []
    monkeypatch.setattr(task_api, "_schedule", lambda tid: scheduled.append(tid),
                        raising=False)

    out = asyncio.run(task_api.resume_task("task_rs2", _FakeRequest()))
    assert out["resumed"] is True and scheduled == ["task_rs2"]
    assert fake_task.status == "paused", "重启后无待答问题 → 回落到 paused 再放行"

# ── 7. 工具面端到端（surface.execute → 阻塞 → answer 唤醒） ─────────


def test_surface_execute_ask_user_end_to_end(monkeypatch):
    """工具面走真实入口：发起提问 → 挂起 → answer 唤醒 → 返回 answered。"""
    from clipwright.tool import surface

    monkeypatch.setattr(ask_user, "MIN_TIMEOUT_SEC", 1)

    async def _go():
        task = SimpleNamespace(id="task_surf", status="running", touch=lambda: None)
        call = asyncio.create_task(surface.execute(
            "ask_user",
            {"question": "选哪个？", "options": _opts(), "allow_free_text": False,
             "default_option_id": "b", "timeout_sec": 10},
            pipeline_id="task_surf", idem_key="k1", task=task))
        await asyncio.sleep(0.05)
        pend = ask_user.pending("task", "task_surf")
        assert len(pend) == 1
        ask_user.answer(pend[0]["question_id"], option_id="a")
        out = await call
        assert out["status"] == "ok"
        assert out["output"]["option_id"] == "a"
        assert out["output"]["status"] == "answered"

    asyncio.run(_go())


def test_surface_execute_ask_user_validation_error(monkeypatch):
    from clipwright.tool import surface

    out = asyncio.run(surface.execute(
        "ask_user",
        {"question": "q", "options": [{"id": "a", "label": "A"}],
         "allow_free_text": False, "timeout_sec": 60},
        pipeline_id="task_bad", idem_key="k2",
        task=SimpleNamespace(id="task_bad", status="running", touch=lambda: None)))
    assert out["status"] == "refused"
    assert out["code"] == "not_answerable"
