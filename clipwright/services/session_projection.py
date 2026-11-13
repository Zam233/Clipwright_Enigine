"""任务事件 → 会话时间线投影（PRD docs/agent-session-timeline-prd-2026-10-31.md §5.3）。

**为什么是投影而不是双写**：task 事件流（`services/task_events.py`）已经是 append-only
的权威执行源，内核每个调用点都往那儿写。如果在每个调用点再直接写一次 session log，
新增一个事件类型就要改两处、还会漏。所以这里做**拉式投影**：按 `task_id` 记住
已投影到哪个 `seq`，把新事件映射成时间线条目追加上去。

三条纪律：

1. **幂等**：条目的 `ref` 由事件的"族主 seq"决定（`tool_result` 归到它 `causation_id`
   指向的 `tool_call` 上），同一个 `ref` 只 patch 不新增——重复投影不会造出重复条目。
2. **顺序**：投影按事件 seq 升序追加，条目 seq 自然递增，与事件发生顺序一致。
3. **不猜**：事件里没有的信息不编（如 `duration_ms` 缺失就不写 0）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from clipwright.config import logger
from clipwright.services import session_log as _session_log

#: 事件类型 → 时间线条目类型。未列出的按 `log` 之外的方式处理：
#: 有内容价值的落到 `content`（meta.kind 保留原始类型），纯机制的丢弃。
TYPE_MAP: dict[str, str] = {
    "task_start": "stage",
    # 内核的 plan/replan 是**执行计划**，不是用户看到的"制作规划书"（那一份由需求侧写
    # `plan` 条目）。两者撞同一个类型会让前端无法区分、轨迹也乱（PRD adaptation C2）。
    "plan": "stage",
    "replan": "stage",
    "step_start": "stage",
    "step_end": "stage",
    "tool_call": "tool",
    "tool_result": "tool",
    "subagent_tool": "tool",
    "subagent_progress": "stage",
    "approval_request": "approval",
    "approval_result": "approval",
    "degradation": "degradation",
    "artifact": "artifact",
    "doc_write": "artifact",
    "thinking": "thinking",
    "report": "report",
    "question": "question",
    "answer": "answer",
    "narration": "content",
    "intent": "content",
    "inject": "content",
    "progress": "stage",
    "agent_run_start": "subagent",
    "agent_run_update": "subagent",
    "agent_run_end": "subagent",
    "task_end": "stage",
    "gate_hit": "content",
    "checkpoint": "content",
    "context_loaded": "content",
}

#: 不投影的机制事件（纯内部信号，进时间线只是噪音）
SKIP: frozenset[str] = frozenset()

#: 内容说话人（`meta.role`）
_ROLE: dict[str, str] = {
    "narration": "assistant",
    "intent": "assistant",
    "inject": "user",
}

#: 视为"任务仍在跑"的终态事件
_TASK_END = ("task_end",)


def _event_ref(task_id: str, event: Any) -> str:
    """族主 ref：`tool_result` 归到它 causation 指向的那条 `tool_call`。"""
    causation = str(getattr(event, "causation_id", "") or "")
    owner = event.seq
    if causation.startswith("seq:"):
        try:
            owner = int(causation[4:])
        except ValueError:
            owner = event.seq
    return f"evt:{task_id}:{owner}"


def _detail(event: Any) -> dict[str, Any]:
    raw = getattr(event, "detail", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _content_for(event: Any) -> Any:
    """条目内容：能看见的内容优先，其次原始 detail（专业视图要能查）。"""
    detail = _detail(event)
    for key in ("delta", "text", "content", "markdown", "summary"):
        value = detail.get(key)
        if isinstance(value, str) and value:
            return value
    return detail


def _meta_for(event: Any) -> dict[str, Any]:
    detail = _detail(event)
    meta: dict[str, Any] = {
        "event_type": event.type,
        "event_seq": int(event.seq),
        "message": str(getattr(event, "message", "") or ""),
    }
    for key in ("phase", "stage", "status", "tool", "skill", "agent", "run_id",
                "duration_ms", "step_id", "chars", "artifact", "path", "title",
                "kind", "done", "error", "truncated", "capability",
                "args_brief", "seq_in_run"):
        if key in detail and detail[key] is not None:
            meta[key] = detail[key]
    role = _ROLE.get(event.type)
    if role:
        meta.setdefault("role", role)
    # 执行计划类事件落到 stage，用 meta.kind 保留语义（前端按 kind 区分展示，
    # `plan` 类型从此只表示"制作规划书"）
    if event.type in ("plan", "replan"):
        meta.setdefault("kind", "execution_plan")
    return meta


class Projector:
    """一个会话的任务事件投影器（按 task_id 记游标，按 ref 合并）。"""

    def __init__(self, session_id: str) -> None:
        self.log = _session_log.get_log(session_id)
        self.session_id = session_id

    # ── 单个任务 ─────────────────────────────

    def project_task(self, task_id: str, *, limit: int = 2000) -> int:
        """把任务的新事件投影进时间线，返回新增/更新的条目数。"""
        from clipwright.services import task_events as _events

        cursor = self.log.task_cursor(task_id)
        events = _events.events_since(task_id, cursor, include_spilled=True)
        if not events:
            return 0
        count = 0
        last = cursor
        for event in events[:limit]:
            try:
                if self._project_event(task_id, event):
                    count += 1
            except Exception as exc:  # noqa: BLE001 — 单条映射失败不能让整段投影中断
                logger.warning("事件投影失败 %s seq=%s: %s", task_id, event.seq, exc)
            last = int(event.seq)
        if last > cursor:
            self.log.set_task_cursor(task_id, last)
        return count

    def _project_event(self, task_id: str, event: Any) -> bool:
        etype = str(getattr(event, "type", "") or "")
        if etype in SKIP:
            return False
        item_type = TYPE_MAP.get(etype)
        if item_type is None:
            # 未登记的类型：有文字内容就落到 content（宁可见，不可静默丢）
            if not str(getattr(event, "message", "") or ""):
                return False
            item_type = "content"
        detail = _detail(event)
        # 技能调用与普通工具在时间线里必须分开（PRD skill-pluginization）：
        # 后端事件已带 kind=skill，这里据此投影成 `skill` 类型。
        if item_type == "tool" and str(detail.get("kind") or "") == "skill":
            item_type = "skill"
        meta = _meta_for(event)
        meta["task_id"] = task_id
        content = _content_for(event)

        # thinking 是**逐块增量**（一次调用能发几百条），必须按"轮"合并成一条，
        # 并且走 begin/chunk/finish——否则每条都重写一次全文，写盘量是 O(n²)。
        if item_type == "thinking":
            return self._project_thinking(task_id, event, detail, meta, content)

        ref = _event_ref(task_id, event)
        existing = self.log.find_by_ref(ref)
        if existing is not None:
            # 同族：状态收尾用 patch，内容后面来的优先（结果比调用时的摘要全）
            patch: dict[str, Any] = {"meta": meta}
            state = self._state_for(etype, detail)
            if state:
                patch["state"] = state
            if isinstance(content, str) and content:
                prev = self.log.get(existing.seq, resolve_content=True) or {}
                prev_text = prev.get("content") if isinstance(prev.get("content"), str) else ""
                if content != prev_text:
                    patch["content"] = content
            elif isinstance(content, dict) and content:
                patch["content"] = content
            self.log.patch(existing.seq, **patch)
            return True

        state = self._state_for(etype, detail) or "done"
        self.log.append(
            type=item_type, content=content, source="task",
            ref=ref, state=state, meta=meta,
        )
        return True

    def _project_thinking(self, task_id: str, event: Any, detail: dict[str, Any],
                          meta: dict[str, Any], content: Any) -> bool:
        """思考增量 → 一轮一条（ref 带 round），`done=true` 收尾。"""
        round_no = int(detail.get("round") or 0)
        ref = f"evt:{task_id}:think:{round_no}"
        meta.pop("delta", None)
        delta = content if isinstance(content, str) else ""
        existing = self.log.find_by_ref(ref)
        done = bool(detail.get("done"))
        if existing is None:
            if not delta and not done:
                return False
            if done and not delta:
                # 只有收尾事件、从头没内容：不造空条目（如实反映"本轮没有思考"）
                return False
            item = self.log.begin(type="thinking", source="task", ref=ref, meta=meta)
            if delta:
                self.log.chunk(item.seq, delta)
            if done:
                self.log.finish(item.seq, state="done", meta={**meta, "done": True})
            return True
        if delta:
            self.log.chunk(existing.seq, delta)
        if done:
            self.log.finish(existing.seq, state="done", meta={**meta, "done": True})
        return True

    @staticmethod
    def _state_for(etype: str, detail: dict[str, Any]) -> Optional[str]:
        """`running`（还没完）→ streaming；`result`（收尾事件）→ done/failed。"""
        if etype in ("tool_call", "approval_request", "step_start",
                     "agent_run_start", "task_start"):
            return "streaming"
        if etype in ("tool_result", "approval_result", "step_end",
                     "agent_run_end", "task_end"):
            status = str(detail.get("status") or "").lower()
            if status in ("failed", "error", "cancelled", "canceled"):
                return "failed"
            return "done"
        return None

    # ── 全部任务 ─────────────────────────────

    def project_all(self) -> int:
        total = 0
        for task_id in self.log.task_ids():
            total += self.project_task(task_id)
        return total

    def task_active(self, task_id: str) -> bool:
        """任务是否还没收尾（决定轮询要不要继续）。"""
        from clipwright.services import task_events as _events

        events = _events.events_since(task_id, self.log.task_cursor(task_id),
                                      include_spilled=True)
        for event in reversed(events):
            if str(getattr(event, "type", "")) in _TASK_END:
                return False
            return True
        # 没有新事件：看历史末条
        history = _events.events(task_id, include_spilled=True)
        if not history:
            return True     # 刚创建、事件还没落地
        return str(history[-1].type) not in _TASK_END


# ── 轮询（活的投影）──────────────────────────

_POLLERS: dict[str, asyncio.Task] = {}
_POLL_INTERVAL = 1.0
_IDLE_STOP = 8          # 连续这么多轮没有新增且任务已收尾就停


async def _poll(session_id: str) -> None:
    projector = Projector(session_id)
    idle = 0
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            added = projector.project_all()
        except Exception as exc:  # noqa: BLE001 — 轮询失败不吵，下一轮再来
            logger.warning("任务投影轮询失败 %s: %s", session_id, exc)
            added = 0
        active = any(projector.task_active(t) for t in projector.log.task_ids())
        if added:
            idle = 0
        else:
            idle += 1
        if not active and idle >= _IDLE_STOP:
            break
    _POLLERS.pop(session_id, None)


def ensure_poller(session_id: str) -> None:
    """确保会话有活的投影轮询（幂等；没有事件循环时静默跳过）。"""
    if not session_id:
        return
    existing = _POLLERS.get(session_id)
    if existing is not None and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return          # 同步上下文（脚本/单测）：靠拉取时 project_all 兜底
    _POLLERS[session_id] = loop.create_task(_poll(session_id))


def project(session_id: str) -> int:
    """同步投影一次（拉取接口用，保证刷新后立刻看到最新任务事件）。"""
    if not session_id:
        return 0
    try:
        return Projector(session_id).project_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务投影失败 %s: %s", session_id, exc)
        return 0


def bind(session_id: str, task_id: str) -> None:
    """把任务登记到会话并启动投影轮询（proceed 建任务后调用一次）。"""
    if not session_id or not task_id:
        return
    try:
        _session_log.get_log(session_id).bind_task(task_id)
    except Exception as exc:  # noqa: BLE001 — 绑定失败不影响开工
        logger.warning("任务绑定会话失败 %s/%s: %s", session_id, task_id, exc)
        return
    ensure_poller(session_id)


def reset() -> None:
    """测试/重置用：停掉所有轮询。"""
    for task in list(_POLLERS.values()):
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    _POLLERS.clear()
