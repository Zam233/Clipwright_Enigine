"""ask_user：主 agent / 需求 agent 主动向用户提问并**阻塞等待**回答（PRD §15）。

为什么单独一个模块：这是第一个"工具返回值由外部事件交付"的能力——工具调用发出问题时
必须挂起当前工具轮，等 `answer` 到达再唤醒。把它塞进通用工具注册表会把等待 / 超时 /
恢复 / 持久化混在一起，所以状态机单独放这里，`tool/surface.py` 只做路由。

约定（与 PRD §15 一致）：
- 一个问题一个 `question_id`，创建幂等、只能回答一次；
- 单 owner 最多 `MAX_PENDING` 个待答；超限返回结构化错误，不建问题、不挂起；
- 超时：有 `default_option_id` → 用默认项生成答案（`source="timeout_default"`）并记
  `ask_user_timeout_default` 降级；没有默认项 → 如实失败，**禁止静默代选**；
- 取消：owner 取消时待答问题置 `cancelled`，后续 answer 返回 409；
- 重启：进程内没有协程能接收答案，历史 pending 一律 `expired`，answer 返回 410。
  持久化文件只用于"重启后知道曾经问过什么"，**不用于恢复旧调用**。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 单个 owner（任务/需求会话）最多同时挂几个待答问题
MAX_PENDING = 3
#: 问题文本与选项长度上限（与 §15.2 的 schema 对齐）
MAX_QUESTION_CHARS = 2000
MAX_FREE_TEXT_CHARS = 2000
MAX_OPTIONS = 8
MIN_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 24 * 3600
DEFAULT_TIMEOUT_SEC = 1800

#: question 状态
PENDING = "pending"
ANSWERED = "answered"
EXPIRED = "expired"
CANCELLED = "cancelled"


class AskUserError(RuntimeError):
    """结构化失败：`code` 给前端映射，`http_status` 给 REST 层。"""

    def __init__(self, code: str, message: str, *, http_status: int = 400,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = dict(detail or {})

    def to_public(self) -> dict[str, Any]:
        return {"status": "error", "code": self.code, "error": self.message,
                **self.detail}


@dataclass
class _Question:
    question_id: str
    owner_type: str            # task | requirements
    owner_id: str
    question: str
    options: list[dict[str, Any]] = field(default_factory=list)
    allow_free_text: bool = False
    default_option_id: Optional[str] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    deadline_at: float = 0.0   # epoch 秒
    context_refs: list[str] = field(default_factory=list)
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    answer: Optional[dict[str, Any]] = None

    def to_public(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "question": self.question,
            "options": self.options,
            "allow_free_text": self.allow_free_text,
            "default_option_id": self.default_option_id,
            "timeout_sec": self.timeout_sec,
            "deadline_at": self.deadline_at,
            "context_refs": self.context_refs,
            "status": self.status,
            "created_at": self.created_at,
            "answer": self.answer,
        }


_QUESTIONS: dict[str, _Question] = {}
_FUTURES: dict[str, asyncio.Future] = {}
#: 已从磁盘加载过的 owner（避免同进程内重复把 pending 判成 expired）
_LOADED: set[tuple[str, str]] = set()


# ── 校验 ──────────────────────────────────────────────────────


def _clean_options(options: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in list(options or [])[:MAX_OPTIONS]:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not oid or not label or oid in seen:
            continue
        seen.add(oid)
        item: dict[str, Any] = {"id": oid[:80], "label": label[:120]}
        detail = str(raw.get("detail") or "").strip()
        if detail:
            item["detail"] = detail[:500]
        out.append(item)
    return out


def _validate(question: str, options: list[dict[str, Any]], allow_free_text: bool,
              default_option_id: Optional[str], timeout_sec: int) -> None:
    if not question.strip():
        raise AskUserError("empty_question", "问题不能为空", http_status=400)
    if len(question) > MAX_QUESTION_CHARS:
        raise AskUserError("question_too_long",
                           f"问题超过 {MAX_QUESTION_CHARS} 字", http_status=400)
    if len(options) < 2 and not allow_free_text:
        raise AskUserError(
            "not_answerable",
            "必须给至少 2 个选项，或允许自由输入（allow_free_text=true）",
            http_status=400)
    if default_option_id and default_option_id not in {o["id"] for o in options}:
        raise AskUserError("invalid_default",
                           f"default_option_id={default_option_id} 不在选项里",
                           http_status=400)
    if not (MIN_TIMEOUT_SEC <= int(timeout_sec) <= MAX_TIMEOUT_SEC):
        raise AskUserError("invalid_timeout",
                           f"timeout_sec 必须在 {MIN_TIMEOUT_SEC}–{MAX_TIMEOUT_SEC} 之间",
                           http_status=400)


# ── 持久化（审计 + 重启后判定 expired；不用于恢复旧调用） ─────────


def _store_path(owner_type: str, owner_id: str) -> Path | None:
    try:
        if owner_type == "task":
            from clipwright.services.workspace import Workspace

            root = Workspace(str(owner_id)).dirs().root
        else:
            from clipwright.services.workspace import workspace_root

            root = workspace_root() / "_sessions" / str(owner_id)
        path = Path(root) / "reports" / "ask_user.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception as exc:  # noqa: BLE001 — 落盘不可用不影响问答本身
        logger.warning("ask_user 持久化路径不可用（%s/%s）: %s", owner_type, owner_id, exc)
        return None


def _persist(owner_type: str, owner_id: str) -> None:
    path = _store_path(owner_type, owner_id)
    if path is None:
        return
    items = [q.to_public() for q in _QUESTIONS.values()
             if q.owner_type == owner_type and q.owner_id == owner_id]
    try:
        path.write_text(json.dumps({"owner_type": owner_type, "owner_id": owner_id,
                                    "questions": items}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ask_user 持久化失败 %s: %s", path, exc)


def _load(owner_type: str, owner_id: str) -> None:
    """首次访问某 owner 时把磁盘记录读进来；历史 pending 一律判 expired。"""
    key = (str(owner_type), str(owner_id))
    if key in _LOADED:
        return
    _LOADED.add(key)
    path = _store_path(owner_type, owner_id)
    if path is None or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ask_user 历史读取失败 %s: %s", path, exc)
        return
    for raw in data.get("questions") or []:
        qid = str(raw.get("question_id") or "")
        if not qid or qid in _QUESTIONS:
            continue
        q = _Question(
            question_id=qid, owner_type=str(raw.get("owner_type") or owner_type),
            owner_id=str(raw.get("owner_id") or owner_id),
            question=str(raw.get("question") or ""),
            options=list(raw.get("options") or []),
            allow_free_text=bool(raw.get("allow_free_text")),
            default_option_id=raw.get("default_option_id"),
            timeout_sec=int(raw.get("timeout_sec") or DEFAULT_TIMEOUT_SEC),
            deadline_at=float(raw.get("deadline_at") or 0.0),
            context_refs=list(raw.get("context_refs") or []),
            status=str(raw.get("status") or PENDING),
            created_at=float(raw.get("created_at") or 0.0),
            answer=raw.get("answer"),
        )
        if q.status == PENDING:
            # 重启：旧协程已不在，答案无处投递 → 如实标记失效，不假装还能回答
            q.status = EXPIRED
            q.answer = {"source": "process_restart",
                        "reason": "服务重启，原问题已失效"}
        _QUESTIONS[qid] = q


# ── 事件 ──────────────────────────────────────────────────────


def _emit(owner_type: str, owner_id: str, q: _Question) -> None:
    if owner_type != "task":
        return  # 需求会话有自己的流，接入见 §15.5（M5c）
    try:
        from clipwright.services.task_events import emit

        emit(owner_id, "question", f"待回答：{q.question[:80]}",
             {"question_id": q.question_id, "owner_type": q.owner_type,
              "owner_id": q.owner_id, "question": q.question,
              "options": q.options, "allow_free_text": q.allow_free_text,
              "default_option_id": q.default_option_id,
              "timeout_sec": q.timeout_sec, "deadline_at": q.deadline_at,
              "context_refs": q.context_refs},
             span="kernel")
    except Exception:  # noqa: BLE001 — 事件通道不可用不影响提问
        pass


def _emit_answer(owner_type: str, owner_id: str, q: _Question, ans: dict[str, Any]) -> None:
    if owner_type != "task":
        return
    try:
        from clipwright.services.task_events import emit

        emit(owner_id, "answer",
             f"已回答：{str(ans.get('option_id') or ans.get('text') or '')[:60]}",
             {"question_id": q.question_id, "option_id": ans.get("option_id"),
              "text": ans.get("text"), "source": ans.get("source"),
              "answered_at": ans.get("answered_at")},
             span="kernel")
    except Exception:  # noqa: BLE001
        pass


# ── 对外 API ─────────────────────────────────────────────────


def create(owner_type: str, owner_id: str, *, question: str,
           options: Iterable[Any] = (), allow_free_text: bool = False,
           default_option_id: Optional[str] = None,
           timeout_sec: int = DEFAULT_TIMEOUT_SEC,
           context_refs: Iterable[str] | None = None) -> dict[str, Any]:
    """建一个问题（不发事件、不挂起）。返回公开结构。"""
    if not owner_id:
        raise AskUserError("missing_owner", "缺少任务/会话标识", http_status=400)
    _load(owner_type, owner_id)
    opts = _clean_options(options)
    _validate(str(question), opts, bool(allow_free_text), default_option_id, timeout_sec)
    pend = [q for q in _QUESTIONS.values()
            if q.owner_type == owner_type and q.owner_id == owner_id
            and q.status == PENDING]
    if len(pend) >= MAX_PENDING:
        raise AskUserError(
            "too_many_pending",
            f"{owner_id} 已有 {len(pend)} 个待答问题（上限 {MAX_PENDING}），"
            "请先回答或用默认项处理",
            http_status=409, detail={"pending": [q.question_id for q in pend]})

    now = time.time()
    q = _Question(
        question_id=f"q_{uuid.uuid4().hex[:12]}",
        owner_type=str(owner_type), owner_id=str(owner_id),
        question=str(question).strip(), options=opts,
        allow_free_text=bool(allow_free_text),
        default_option_id=default_option_id,
        timeout_sec=int(timeout_sec),
        deadline_at=now + int(timeout_sec),
        context_refs=[str(x)[:200] for x in (context_refs or [])][:20],
    )
    _QUESTIONS[q.question_id] = q
    _persist(owner_type, owner_id)
    _emit(owner_type, owner_id, q)
    return q.to_public()


def get(question_id: str) -> dict[str, Any] | None:
    q = _QUESTIONS.get(str(question_id))
    return q.to_public() if q else None


def pending(owner_type: str, owner_id: str) -> list[dict[str, Any]]:
    _load(owner_type, owner_id)
    return [q.to_public() for q in _QUESTIONS.values()
            if q.owner_type == owner_type and q.owner_id == owner_id
            and q.status == PENDING]


def list_for_owner(owner_type: str, owner_id: str) -> list[dict[str, Any]]:
    _load(owner_type, owner_id)
    items = [q.to_public() for q in _QUESTIONS.values()
             if q.owner_type == owner_type and q.owner_id == owner_id]
    return sorted(items, key=lambda d: d.get("created_at") or 0.0)


def answer(question_id: str, *, option_id: str | None = None, text: str | None = None,
           source: str | None = None) -> dict[str, Any]:
    """回答一个问题。唯一入口：REST `/answer` 与测试都走这里。

    错误码（映射到 HTTP）：`question_not_found` 404、`already_answered` 409、
    `question_expired` 410、`question_cancelled` 409、`invalid_option` 400 等。
    """
    qid = str(question_id or "")
    q = _QUESTIONS.get(qid)
    if q is None:
        raise AskUserError(
            "question_not_found",
            "问题不存在，或服务重启后该问题已失效",
            http_status=410, detail={"question_id": qid})
    if q.status == ANSWERED:
        raise AskUserError("already_answered", "该问题已回答过一次",
                           http_status=409,
                           detail={"question_id": qid, "answer": q.answer})
    if q.status in (EXPIRED,):
        raise AskUserError("question_expired", "该问题已失效（超时或服务重启）",
                           http_status=410, detail={"question_id": qid})
    if q.status == CANCELLED:
        raise AskUserError("question_cancelled", "任务已取消，问题不再接受回答",
                           http_status=409, detail={"question_id": qid})

    opt = str(option_id or "").strip() or None
    txt = (text or "").strip()
    if opt is None and not txt:
        raise AskUserError("empty_answer", "请选择一项，或填写自由文本",
                           http_status=400, detail={"question_id": qid})
    if opt is not None and opt not in {o["id"] for o in q.options}:
        raise AskUserError("invalid_option", f"选项 {opt} 不在该问题里",
                           http_status=400, detail={"question_id": qid})
    if opt is None and not q.allow_free_text:
        raise AskUserError("free_text_not_allowed", "该问题不接受自由输入",
                           http_status=400, detail={"question_id": qid})
    if len(txt) > MAX_FREE_TEXT_CHARS:
        raise AskUserError("answer_too_long",
                           f"回答超过 {MAX_FREE_TEXT_CHARS} 字", http_status=400,
                           detail={"question_id": qid})

    src = source or ("user_option" if opt else "user_text")
    ans = {"option_id": opt, "text": txt or None, "source": src,
           "answered_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    q.status = ANSWERED
    q.answer = ans
    _persist(q.owner_type, q.owner_id)
    fut = _FUTURES.get(qid)
    if fut is not None and not fut.done():
        try:
            fut.set_result(ans)
        except Exception as exc:  # noqa: BLE001 — 事件循环不同时退回事件通知
            logger.warning("ask_user 唤醒等待失败 %s: %s", qid, exc)
    _emit_answer(q.owner_type, q.owner_id, q, ans)
    return {"question_id": qid, **ans}


def cancel_owner(owner_type: str, owner_id: str, *, reason: str = "cancelled") -> int:
    """owner 取消/终止时把它的待答问题作废（answer 之后返回 409）。"""
    _load(owner_type, owner_id)
    n = 0
    for q in _QUESTIONS.values():
        if q.owner_type == owner_type and q.owner_id == owner_id and q.status == PENDING:
            q.status = CANCELLED
            q.answer = {"source": reason, "reason": "任务已取消"}
            n += 1
            fut = _FUTURES.get(q.question_id)
            if fut is not None and not fut.done():
                fut.cancel()
    if n:
        _persist(owner_type, owner_id)
    return n


def expire_owner(owner_type: str, owner_id: str) -> int:
    """把 owner 的 pending 标失效（重启恢复路径用）。返回处理条数。"""
    _load(owner_type, owner_id)
    n = 0
    for q in _QUESTIONS.values():
        if q.owner_type == owner_type and q.owner_id == owner_id and q.status == PENDING:
            q.status = EXPIRED
            q.answer = {"source": "process_restart",
                        "reason": "服务重启，原问题已失效"}
            n += 1
    if n:
        _persist(owner_type, owner_id)
    return n


def has_pending(owner_type: str, owner_id: str) -> bool:
    return bool(pending(owner_type, owner_id))


async def wait_for_answer(question_id: str, *, timeout_sec: float) -> dict[str, Any]:
    """等一个问题的答案（内部用）。超时抛 `AskUserError(question_timeout)`。"""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _FUTURES[str(question_id)] = fut
    try:
        return await asyncio.wait_for(fut, timeout=max(1.0, float(timeout_sec)))
    except asyncio.TimeoutError as exc:
        raise AskUserError("question_timeout",
                           f"等待回答超时（{int(timeout_sec)}s）",
                           http_status=408,
                           detail={"question_id": str(question_id)}) from exc
    finally:
        _FUTURES.pop(str(question_id), None)


async def ask(*, owner_type: str = "task", owner_id: str, question: str,
              options: Iterable[Any] = (), allow_free_text: bool = False,
              default_option_id: Optional[str] = None,
              timeout_sec: int = DEFAULT_TIMEOUT_SEC,
              context_refs: Iterable[str] | None = None,
              task: Any = None) -> dict[str, Any]:
    """提问并**阻塞**等待；超时用默认项（记降级），没有默认项如实失败。

    `task` 传了就顺带把任务状态切到 `waiting_user`（PRD §15.3），结束后恢复。
    """
    q = create(owner_type, owner_id, question=question, options=options,
               allow_free_text=allow_free_text, default_option_id=default_option_id,
               timeout_sec=timeout_sec, context_refs=context_refs)
    qid = q["question_id"]
    prev_status = getattr(task, "status", None)
    if task is not None and prev_status not in (None, "waiting_user"):
        try:
            task.status = "waiting_user"
            if hasattr(task, "touch"):
                task.touch()
        except Exception:  # noqa: BLE001 — 状态切换失败不影响提问本身
            task = None
    try:
        ans = await wait_for_answer(qid, timeout_sec=timeout_sec)
        return {"status": "answered", "question_id": qid, **ans}
    except AskUserError as exc:
        if exc.code != "question_timeout":
            raise
        if not default_option_id:
            _mark_expired(qid)
            raise AskUserError(
                "question_timeout", "等待回答超时，且没有默认项可用",
                http_status=408, detail={"question_id": qid}) from exc
        ans = answer(qid, option_id=str(default_option_id), source="timeout_default")
        try:
            from clipwright.services.degradation import note_degradation

            note_degradation(
                "ask_user_timeout_default",
                detail=f"问题 {qid} 超时，用了默认项 {default_option_id}",
                scope=str(owner_id), impact="按默认方案继续，可能与用户偏好不一致")
        except Exception:  # noqa: BLE001 — 降级记录失败不影响继续
            pass
        return {"status": "answered", "timeout": True, "question_id": qid, **ans}
    finally:
        if task is not None and prev_status in ("running", "planning"):
            try:
                task.status = prev_status
                if hasattr(task, "touch"):
                    task.touch()
            except Exception:  # noqa: BLE001
                pass


def _mark_expired(question_id: str) -> None:
    q = _QUESTIONS.get(str(question_id))
    if q is not None and q.status == PENDING:
        q.status = EXPIRED
        q.answer = {"source": "timeout", "reason": "等待回答超时且无默认项"}
        _persist(q.owner_type, q.owner_id)


def reset() -> None:
    """测试用：清空内存状态（不删磁盘）。"""
    _QUESTIONS.clear()
    _FUTURES.clear()
    _LOADED.clear()


__all__ = [
    "MAX_PENDING", "DEFAULT_TIMEOUT_SEC", "PENDING", "ANSWERED", "EXPIRED", "CANCELLED",
    "AskUserError", "answer", "ask", "cancel_owner", "create", "expire_owner", "get",
    "has_pending", "list_for_owner", "pending", "reset", "wait_for_answer",
]
