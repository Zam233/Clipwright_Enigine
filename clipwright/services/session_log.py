"""会话时间线唯一日志（PRD docs/agent-session-timeline-prd-2026-10-31.md）。

**一个会话只有一条有序流**。位置（`seq`）由服务端在条目创建时分配，前端只渲染，
不再推断、不再合并第二数据源——这是"思考沉底 / 简报卡错位 / 刷新后混乱"三类缺陷
的根治办法：只要位置是客户端推断出来的，任何一次异步时序变化都会让它错乱。

落盘布局（会话目录，默认在工作区根的 `sessions/` 下）::

    sessions/<session_id>/
    ├── session.json          # 索引：轻量条目，按 seq 升序，原子替换
    ├── session.jsonl         # append-only 操作日志：begin / append / patch / finish
    ├── items/<seq>.json      # 大 payload（thinking 全文、report、plan…）
    └── archive/part-<n>.jsonl# 超上限后的归档段

五条硬约束（PRD §0）：

1. `seq` 单调唯一，由服务端分配；`time` 只作展示与跨会话对账。
2. 流式条目**先占位、后填充**：`begin` 拿 seq，`chunk` 追加，`finish` 收尾，全程同一个 seq。
3. 写盘节流（200ms / 4KB）+ 原子替换（`.tmp` + `os.replace`）。
4. 大 payload 外置，`session.json` 只留索引与引用。
5. 有上限：多则归档、超长则截断并**明确标注**（`meta.truncated`），不静默丢。

进程重启后：`state == "streaming"` 且 jsonl 里没有 `finish` 的条目 → 标 `interrupted`，
内容取最后一次 flush 的快照。**不丢、不假装完成。**
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import TIME_ZONE, logger

# ── 类型契约（PRD §3.2）─────────────────────────────

#: PRD 明确的七种核心类型
CORE_TYPES: tuple[str, ...] = (
    "thinking",   # 模型思考（流式，正文外置）
    "content",    # 正文/助手回复（流式）
    "tool",       # 工具调用（running → done，同 seq upsert）
    "skill",      # 技能调用
    "subagent",   # 子 agent 一次派发
    "report",     # 创意简报（结构化，外置）
    "plan",       # 规划书（结构化 + brief_hash，外置）
)

#: 扩展类型：ask_user 与专业视图需要按位置出现的东西。
#: 它们同样有 seq/time，只是不参与"七类"的对外表述。
EXTRA_TYPES: tuple[str, ...] = (
    "question",     # agent 提问
    "answer",       # 用户回答
    "approval",     # 审批请求/结果
    "degradation",  # 降级记录
    "stage",        # 阶段进度（规划书生成的结构/翻译）
    "artifact",     # 产物（成片/文档/文件写入）
)

ITEM_TYPES: tuple[str, ...] = CORE_TYPES + EXTRA_TYPES

STATES: tuple[str, ...] = ("streaming", "done", "failed", "interrupted")

#: 永远外置的类型（内容可能极大）
ALWAYS_EXTERNAL: frozenset[str] = frozenset({"thinking", "report", "plan"})

#: `patch` 允许改的字段（其余不可变：seq / time / type / turn / source）
_PATCHABLE: frozenset[str] = frozenset({"state", "meta", "content", "ref"})

# ── 可调参数（PRD §4.2 / §4.3）────────────────────

#: 流式落盘节流：200ms 或累计 4KB
FLUSH_INTERVAL_MS = int(os.environ.get("CLIPWRIGHT_SESSION_FLUSH_MS", "200"))
FLUSH_BYTES = int(os.environ.get("CLIPWRIGHT_SESSION_FLUSH_BYTES", "4096"))
#: 内联阈值：超过就外置到 items/<seq>.json
INLINE_LIMIT = int(os.environ.get("CLIPWRIGHT_SESSION_INLINE_LIMIT", "8192"))
#: 条目上限；超过归档到 archive/，保留最近 KEEP_AFTER_COMPACT 条
MAX_ITEMS = int(os.environ.get("CLIPWRIGHT_SESSION_MAX_ITEMS", "5000"))
KEEP_AFTER_COMPACT = int(os.environ.get("CLIPWRIGHT_SESSION_KEEP_ITEMS", "4000"))
#: 单条文本上限（40 万字）；超出截断并标注
MAX_TEXT_CHARS = int(os.environ.get("CLIPWRIGHT_SESSION_MAX_TEXT", "400000"))
#: 单次拉取上限
MAX_PAGE = 500

# ── 内部状态 ─────────────────────────────────────

_LOGS: dict[str, "SessionLog"] = {}
_REGISTRY_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(tz=TIME_ZONE).isoformat(timespec="milliseconds")


def _safe_id(session_id: str) -> str:
    safe = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_.")
    return safe or "session"


def _payload_size(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 — 不可序列化就按大算，走外置
        return INLINE_LIMIT + 1


def sessions_root() -> Path:
    """会话根目录。

    默认 `workspace_root()/sessions`：会话在"项目"存在之前就已创建（新建制作第一步
    就是开需求会话），所以不能按 project 分目录；放到工作区根下既与项目目录同根、
    又能跨项目排查。`CLIPWRIGHT_SESSION_LOG_DIR` 可覆盖（测试/隔离用）。
    """
    override = os.environ.get("CLIPWRIGHT_SESSION_LOG_DIR")
    if override:
        root = Path(override)
    else:
        from clipwright.services.workspace import workspace_root

        root = workspace_root() / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── item ────────────────────────────────────────


@dataclass
class Item:
    """时间线一条。`seq` / `time` 创建后永不改写。"""

    seq: int
    type: str
    time: str
    turn: Optional[int] = None
    state: str = "done"
    source: str = "requirements"
    ref: Optional[str] = None
    content_ref: Optional[str] = None
    content_inline: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "time": self.time,
            "turn": self.turn,
            "state": self.state,
            "source": self.source,
            "ref": self.ref,
            "content_ref": self.content_ref,
            "content_inline": self.content_inline,
            "meta": dict(self.meta or {}),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Item":
        return cls(
            seq=int(raw.get("seq") or 0),
            type=str(raw.get("type") or "log"),
            time=str(raw.get("time") or _now_iso()),
            turn=(int(raw["turn"]) if raw.get("turn") is not None else None),
            state=str(raw.get("state") or "done"),
            source=str(raw.get("source") or "requirements"),
            ref=(str(raw["ref"]) if raw.get("ref") else None),
            content_ref=(str(raw["content_ref"]) if raw.get("content_ref") else None),
            content_inline=raw.get("content_inline"),
            meta=dict(raw.get("meta") or {}),
        )


def _safe_put(queue: Any, frame: dict[str, Any]) -> None:
    """往订阅队列投一帧；满了丢最老的（续接靠 since_seq 补齐，不能堵住写侧）。"""
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(frame)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 — 队列已关闭等
        pass


class SessionLog:
    """一个会话的时间线（唯一写入入口）。"""

    def __init__(self, session_id: str, base: Optional[Path] = None,
                 project_id: str = "") -> None:
        self.session_id = str(session_id)
        self.dir = Path(base or sessions_root()) / _safe_id(session_id)
        self._lock = threading.RLock()
        self._items: dict[int, Item] = {}
        self._next_seq = 1
        self._counters: dict[str, int] = {}
        self._archived_until = 0
        self._created_at = ""
        self._updated_at = ""
        self._project_id = project_id
        self._task_ids: list[str] = []
        self._task_cursors: dict[str, int] = {}
        self._loaded = False
        self._dirty = False
        #: 流式缓冲：seq → 已收到的增量片段（只在内存，flush 时才落盘）
        self._buffers: dict[int, list[str]] = {}
        self._last_flush: dict[int, float] = {}
        #: SSE 订阅：(queue, loop)
        self._subs: list[tuple[Any, Any]] = []
        #: jsonl 已写过的 op（恢复用）
        self._finished: set[int] = set()

    # ── 路径 ─────────────────────────────────

    @property
    def index_path(self) -> Path:
        return self.dir / "session.json"

    @property
    def journal_path(self) -> Path:
        return self.dir / "session.jsonl"

    @property
    def items_dir(self) -> Path:
        return self.dir / "items"

    @property
    def archive_dir(self) -> Path:
        return self.dir / "archive"

    def _ensure_dirs(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ── 载入与恢复 ────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # 注意：**不在这里建目录**。读路径（GET /timeline）对一个从没写过条目的会话
        # 会顺手 mkdir，运维上就会看到一堆"存在但空"的会话目录，无法判断到底有没有记录
        # （2026-10-31 实测踩到）。目录只在真正要写盘时创建。
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                self._created_at = str(raw.get("created_at") or "")
                self._updated_at = str(raw.get("updated_at") or "")
                self._next_seq = int(raw.get("next_seq") or 1)
                self._archived_until = int(raw.get("archived_until_seq") or 0)
                self._counters = {str(k): int(v) for k, v in (raw.get("counters") or {}).items()}
                self._task_ids = [str(t) for t in (raw.get("task_ids") or []) if t]
                self._task_cursors = {
                    str(k): int(v) for k, v in (raw.get("task_cursors") or {}).items()
                }
                if not self._project_id:
                    self._project_id = str(raw.get("project_id") or "")
                for item_raw in raw.get("items") or []:
                    try:
                        item = Item.from_dict(item_raw)
                    except Exception:  # noqa: BLE001
                        continue
                    if item.seq > 0:
                        self._items[item.seq] = item
            except Exception as exc:  # noqa: BLE001 — 索引坏了不能连累会话继续写
                logger.warning("会话索引读取失败 %s: %s（按 append-only 日志重建）", self.dir, exc)
        self._recover()
        if not self._created_at:
            self._created_at = _now_iso()
        if not self._updated_at:
            self._updated_at = self._created_at
        self._loaded = True

    def _recover(self) -> None:
        """扫 append-only 日志：把"起了但没结束"的流式条目标成 interrupted。

        只在首次载入（= 进程重启后第一次碰到这个会话）执行；运行中的会话在内存里，
        不会被自己的未完成状态判死。
        """
        last_op: dict[int, str] = {}
        max_seq = 0
        if self.journal_path.exists():
            try:
                with self.journal_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:  # noqa: BLE001 — 半行（崩溃写一半）跳过
                            continue
                        seq = int(rec.get("seq") or 0)
                        if seq <= 0:
                            continue
                        max_seq = max(max_seq, seq)
                        op = str(rec.get("op") or "")
                        last_op[seq] = op
                        if op == "finish":
                            self._finished.add(seq)
            except Exception as exc:  # noqa: BLE001
                logger.warning("会话日志读取失败 %s: %s", self.journal_path, exc)
        if max_seq >= self._next_seq:
            self._next_seq = max_seq + 1
        repaired = 0
        for seq, item in self._items.items():
            if item.state != "streaming":
                continue
            if seq in self._finished or last_op.get(seq) in ("finish",):
                item.state = "done"
                continue
            item.state = "interrupted"
            item.meta["interrupted"] = True
            repaired += 1
        if repaired:
            logger.info("会话 %s：%d 条未完成流式条目已标 interrupted", self.session_id, repaired)
            # 只修内存镜像，等下一次真实写入时落盘（不在载入时制造 IO）

    # ── 持久化 ───────────────────────────────

    def _write_json(self, path: Path, payload: Any) -> None:
        self._ensure_dirs()
        tmp = path.with_name(path.name + ".tmp")
        data = json.dumps(payload, ensure_ascii=False)
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)

    def _append_journal(self, record: dict[str, Any]) -> None:
        try:
            self._ensure_dirs()
            with self.journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — 日志写失败不能让对话中断
            logger.warning("会话日志追加失败 %s: %s", self.journal_path, exc)

    def _snapshot(self) -> dict[str, Any]:
        ordered = [self._items[s].to_dict() for s in sorted(self._items)]
        return {
            "version": 1,
            "session_id": self.session_id,
            "project_id": self._project_id,
            "created_at": self._created_at,
            "updated_at": self._updated_at,
            "next_seq": self._next_seq,
            "archived_until_seq": self._archived_until,
            "counters": dict(self._counters),
            "task_ids": list(self._task_ids),
            "task_cursors": dict(self._task_cursors),
            "items": ordered,
        }

    def _store_content(self, item: Item, payload: Any) -> None:
        """按类型/大小决定内联还是外置，并把载入时读到的旧引用清掉。"""
        size = _payload_size(payload)
        external = item.type in ALWAYS_EXTERNAL or size > INLINE_LIMIT
        if not external:
            item.content_ref = None
            item.content_inline = payload
            return
        rel = f"items/{item.seq}.json"
        try:
            self._write_json(self.dir / rel, {"seq": item.seq, "type": item.type,
                                              "content": payload})
            item.content_ref = rel
            item.content_inline = None
        except Exception as exc:  # noqa: BLE001 — 外置失败就内联兜住，宁可文件大
            logger.warning("会话条目外置失败 seq=%s: %s", item.seq, exc)
            item.content_ref = None
            item.content_inline = payload

    def _truncate(self, item: Item, text: str) -> str:
        if len(text) <= MAX_TEXT_CHARS:
            return text
        item.meta["truncated"] = True
        item.meta["chars_full"] = len(text)
        return text[:MAX_TEXT_CHARS]

    def _flush_locked(self, *, force: bool = False) -> None:
        """把内存缓冲与索引写盘（调用方必须持锁）。"""
        if not self._dirty and not force:
            return
        # 1) 先把流式缓冲并进条目（文本类型才截断）
        for seq, parts in list(self._buffers.items()):
            item = self._items.get(seq)
            if item is None:
                self._buffers.pop(seq, None)
                continue
            text = "".join(parts)
            if not text:
                continue
            if item.state == "streaming":
                text = self._truncate(item, text)
            self._store_content(item, text)
            item.meta["chars"] = len(text)
        # 2) 归档（超上限）
        self._compact_locked()
        # 3) 索引原子替换
        self._updated_at = _now_iso()
        try:
            self._write_json(self.index_path, self._snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话索引写入失败 %s: %s", self.index_path, exc)
        self._dirty = False

    def _compact_locked(self) -> None:
        if len(self._items) <= MAX_ITEMS:
            return
        seqs = sorted(self._items)
        drop = max(0, len(seqs) - KEEP_AFTER_COMPACT)
        if drop <= 0:
            return
        victims = seqs[:drop]
        part = self._archived_until and int(self._archived_until / max(1, KEEP_AFTER_COMPACT)) or 0
        part_path = self.archive_dir / f"part-{part + 1:04d}.jsonl"
        try:
            with part_path.open("a", encoding="utf-8") as fh:
                for seq in victims:
                    item = self._items[seq]
                    fh.write(json.dumps({"op": "archive", "item": item.to_dict()},
                                        ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — 归档失败就先不丢，宁可超上限
            logger.warning("会话归档失败 %s: %s", part_path, exc)
            return
        for seq in victims:
            item = self._items.pop(seq)
            self._buffers.pop(seq, None)
            self._last_flush.pop(seq, None)
            if item.content_ref:
                try:
                    (self.dir / item.content_ref).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
        self._archived_until = victims[-1]
        # 重写 journal：只保留在册条目，避免无限增长（旧历史已在 archive 里）
        try:
            self._ensure_dirs()
            tmp = self.journal_path.with_name(self.journal_path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for seq in sorted(self._items):
                    item = self._items[seq]
                    fh.write(json.dumps(
                        {"op": "begin", "seq": seq, "type": item.type,
                         "time": item.time, "turn": item.turn}, ensure_ascii=False) + "\n")
                    if item.state != "streaming":
                        fh.write(json.dumps({"op": "finish", "seq": seq,
                                             "state": item.state},
                                            ensure_ascii=False) + "\n")
            os.replace(tmp, self.journal_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话日志重写失败 %s: %s", self.journal_path, exc)

    # ── 广播 ─────────────────────────────────

    def subscribe(self) -> tuple[Any, Any]:
        """订阅实时帧，返回 `(queue, loop)`；调用方负责 `unsubscribe`。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.append((queue, loop))
        return queue, loop

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            self._subs = [(q, lp) for q, lp in self._subs if q is not queue]

    def _publish(self, frame: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for queue, loop in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, frame)
            except RuntimeError:
                pass
            except Exception:  # noqa: BLE001
                pass

    def _frame_item(self, item: Item) -> dict[str, Any]:
        """发布帧里的条目：**必须带已解析的 content**。

        踩过的坑（2026-10-31 E2E 实测）：`report`/`plan` 这类大内容走外置，
        `to_dict()` 里只有 `content_ref`、`content_inline=None`。如果帧里不带 content，
        前端实时收到一条"没有内容的简报条目"→ 卡片根本不渲染，只有刷新后才出现。
        所以这里把解析结果一并带上；超大内容（>64KB）才省略，让前端按需回 REST 取。
        """
        payload = item.to_dict()
        if item.content_ref:
            try:
                content = self.resolve(item)
            except Exception:  # noqa: BLE001 — 读不到就退回内联（可能为 None）
                content = item.content_inline
        else:
            content = item.content_inline
        if content is not None and _payload_size(content) <= 64 * 1024:
            payload["content"] = content
        return payload

    # ── 写入口（唯一）────────────────────────

    def append(
        self,
        *,
        type: str,
        content: Any = None,
        source: str = "requirements",
        turn: Optional[int] = None,
        ref: Optional[str] = None,
        state: str = "done",
        meta: Optional[dict[str, Any]] = None,
        time_text: Optional[str] = None,
    ) -> Item:
        """新增一条已完成条目。"""
        return self._create(
            type=type, content=content, source=source, turn=turn, ref=ref,
            state=state, meta=meta, time_text=time_text,
        )

    def begin(
        self,
        *,
        type: str,
        source: str = "requirements",
        turn: Optional[int] = None,
        ref: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
        time_text: Optional[str] = None,
    ) -> Item:
        """开一个流式条目（先占位，拿到 seq）。"""
        return self._create(
            type=type, content=None, source=source, turn=turn, ref=ref,
            state="streaming", meta=meta, time_text=time_text,
        )

    def _create(
        self,
        *,
        type: str,
        content: Any,
        source: str,
        turn: Optional[int],
        ref: Optional[str],
        state: str,
        meta: Optional[dict[str, Any]],
        time_text: Optional[str],
        flush: bool = True,
    ) -> Item:
        if type not in ITEM_TYPES:
            raise ValueError(f"未知时间线条目类型: {type}（可选 {ITEM_TYPES}）")
        if state not in STATES:
            raise ValueError(f"未知 state: {state}（可选 {STATES}）")
        with self._lock:
            self._ensure_loaded()
            seq = self._next_seq
            self._next_seq = seq + 1
            item = Item(
                seq=seq, type=type, time=time_text or _now_iso(), turn=turn,
                state=state, source=source, ref=ref, meta=dict(meta or {}),
            )
            # 先记日志（崩溃恢复的唯一依据），再落索引
            self._append_journal({"op": "begin", "seq": seq, "type": type,
                                  "time": item.time, "turn": turn, "state": state})
            if content is not None:
                if isinstance(content, str):
                    if len(content) > MAX_TEXT_CHARS:
                        full = len(content)
                        content = self._truncate(item, content)
                        item.meta.setdefault("chars_full", full)
                    item.meta["chars"] = len(content)
                self._store_content(item, content)
            self._items[seq] = item
            self._counters[type] = self._counters.get(type, 0) + 1
            if state == "streaming":
                self._buffers[seq] = []
                self._last_flush[seq] = time.monotonic()
            else:
                self._finished.add(seq)
                self._append_journal({"op": "finish", "seq": seq, "state": state})
            self._dirty = True
            if flush:
                self._flush_locked(force=True)
            payload = item.to_dict()
        # 批量回填时按调用方要求延后广播（一次回填 = 一帧 snapshot 更划算）
        self._publish({"op": "append", "item": self._frame_item(item)})
        return item

    # ── 存量会话回填（T1，PRD agent-timeline-adaptation §4）──────

    def is_empty(self) -> bool:
        """一条条目都没有（回填的唯一触发条件）。"""
        with self._lock:
            self._ensure_loaded()
            return not self._items

    def backfill_messages(
        self,
        messages: Any,
        *,
        brief: Any = None,
        plan: Any = None,
        brief_hash_fn: Any = None,
    ) -> int:
        """把需求会话既有的 `messages` 回填成时间线条目（**只做一次**）。

        背景（2026-10-31 实测）：条目只在「新建会话 / 发生一轮对话」时写。升级前建的
        会话磁盘上没有条目，前端打开就是空的——用户看到「还没有可显示的内容」。
        这里把会话里**还留着**的消息补成条目，让老会话能正常显示。

        诚实的边界：
        - `compress_history` 会丢掉 20 轮以前的历史，所以回填只保证"当前会话里还在的
          消息"；每条的 `meta.backfilled=True`，`meta.backfill_limited=True` 供前端注明；
        - **不**回填历史 `thinking`：旧事件流只有 `turn_ts`，没有精确位置，塞进去等于
          重新造一次"客户端推断位置"的老毛病。
        """
        with self._lock:
            self._ensure_loaded()
            if self._items:
                return 0
            rows = [m for m in (messages or []) if isinstance(m, dict)]
            turn = 0
            written = 0
            for idx, msg in enumerate(rows):
                role = str(msg.get("role") or "assistant")
                text = msg.get("content")
                if not isinstance(text, str) or not text.strip():
                    continue
                if role == "user":
                    turn += 1
                meta = {
                    "role": role if role in ("user", "assistant", "system") else "assistant",
                    "backfilled": True,
                    "backfill_limited": True,
                    "legacy_index": idx,
                }
                ts = msg.get("timestamp")
                self._create(
                    type="content", content=text, source="requirements",
                    turn=turn, ref=f"legacy:msg:{msg.get('id') or idx}",
                    state="done", meta=meta,
                    time_text=(ts if isinstance(ts, str) and ts else None),
                    flush=False,
                )
                written += 1
            hash_of = brief_hash_fn or (lambda _b: "")
            turn_for_cards = max(1, turn)
            if isinstance(brief, dict) and brief:
                bh = str(hash_of(brief) or "")
                self._create(
                    type="report", content=brief, source="requirements",
                    turn=turn_for_cards, ref=f"legacy:report:{bh or 'brief'}",
                    state="done",
                    meta={"role": "assistant", "backfilled": True,
                          "title": str(brief.get("title") or ""), "brief_hash": bh,
                          "kind": "production_plan_brief"},
                    time_text=None, flush=False,
                )
                written += 1
            if isinstance(plan, dict) and plan:
                bh = str(hash_of(brief) or "") if isinstance(brief, dict) else ""
                scenes = plan.get("raw_scenes") or plan.get("scenes") or []
                self._create(
                    type="plan", content=plan, source="requirements",
                    turn=turn_for_cards, ref=f"legacy:plan:{bh or 'plan'}",
                    state="done",
                    meta={"role": "assistant", "backfilled": True,
                          "title": str(plan.get("title") or ""), "brief_hash": bh,
                          "scene_count": len(scenes) if isinstance(scenes, list) else 0,
                          "kind": "production_plan"},
                    time_text=None, flush=False,
                )
                written += 1
            if not written:
                return 0
            self._dirty = True
            self._flush_locked(force=True)
        # 回填完整体广播一次，别让前端逐条追加（顺序已在 seq 里定好）
        self._publish({"op": "snapshot", **self.list_items(since_seq=0, limit=MAX_PAGE,
                                                           resolve_content=True)})
        logger.info("会话 %s 回填 %d 条时间线条目（来源 session.messages）",
                    self.session_id, written)
        return written

    def chunk(self, seq: int, delta: str) -> None:
        """追加流式增量（只进内存缓冲，按节流窗落盘；SSE 立刻推送）。"""
        if not delta:
            return
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(int(seq))
            if item is None or item.state != "streaming":
                return
            parts = self._buffers.setdefault(int(seq), [])
            parts.append(str(delta))
            size = sum(len(p) for p in parts)
            now = time.monotonic()
            last = self._last_flush.get(int(seq), 0.0)
            self._dirty = True
            if size >= FLUSH_BYTES or (now - last) * 1000.0 >= FLUSH_INTERVAL_MS:
                self._last_flush[int(seq)] = now
                self._flush_locked()
        self._publish({"op": "chunk", "seq": int(seq), "delta": str(delta)})

    def finish(
        self,
        seq: int,
        *,
        content: Optional[str] = None,
        state: str = "done",
        meta: Optional[dict[str, Any]] = None,
    ) -> Optional[Item]:
        """流式收尾：合并缓冲（或直接给全文）、落盘、广播。"""
        if state not in STATES:
            raise ValueError(f"未知 state: {state}（可选 {STATES}）")
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(int(seq))
            if item is None:
                return None
            parts = self._buffers.pop(int(seq), [])
            text = content if content is not None else "".join(parts)
            if isinstance(text, str) and text:
                text = self._truncate(item, text)
                self._store_content(item, text)
                item.meta["chars"] = len(text)
            item.state = state
            item.meta.update(meta or {})
            if state == "interrupted":
                item.meta["interrupted"] = True
            self._finished.add(int(seq))
            self._append_journal({"op": "finish", "seq": int(seq), "state": state})
            self._dirty = True
            self._flush_locked(force=True)
            payload = item.to_dict()
        self._publish({"op": "finish", "seq": int(seq), "item": self._frame_item(item)})
        return item

    def patch(self, seq: int, **fields: Any) -> Optional[Item]:
        """同 seq 更新（tool running→done、状态/元信息）。不可变字段白名单之外忽略。"""
        unknown = set(fields) - _PATCHABLE
        if unknown:
            raise ValueError(f"patch 不允许改这些字段: {sorted(unknown)}")
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(int(seq))
            if item is None:
                return None
            if "state" in fields:
                state = str(fields["state"])
                if state not in STATES:
                    raise ValueError(f"未知 state: {state}")
                item.state = state
                if state != "streaming":
                    self._finished.add(int(seq))
                    self._buffers.pop(int(seq), None)
            if "meta" in fields and isinstance(fields["meta"], dict):
                item.meta.update(fields["meta"])
            if "ref" in fields:
                item.ref = str(fields["ref"]) if fields["ref"] else None
            if "content" in fields:
                payload = fields["content"]
                if isinstance(payload, str):
                    payload = self._truncate(item, payload)
                    item.meta["chars"] = len(payload)
                self._store_content(item, payload)
            self._append_journal({"op": "patch", "seq": int(seq),
                                  "fields": {k: v for k, v in fields.items() if k != "content"}})
            self._dirty = True
            self._flush_locked(force=True)
            updated = item.to_dict()
        self._publish({"op": "patch", "seq": int(seq), "item": self._frame_item(item)})
        return item

    def set_project(self, project_id: str) -> None:
        with self._lock:
            self._ensure_loaded()
            if project_id and project_id != self._project_id:
                self._project_id = str(project_id)
                self._dirty = True
                self._flush_locked(force=True)

    def bind_task(self, task_id: str) -> None:
        """登记"这个任务属于本会话"（task 事件投影的入口，PRD §5.3）。"""
        tid = str(task_id or "").strip()
        if not tid:
            return
        with self._lock:
            self._ensure_loaded()
            if tid in self._task_ids:
                return
            self._task_ids.append(tid)
            self._dirty = True
            self._flush_locked(force=True)

    def task_ids(self) -> list[str]:
        with self._lock:
            self._ensure_loaded()
            return list(self._task_ids)

    def task_cursor(self, task_id: str) -> int:
        with self._lock:
            self._ensure_loaded()
            return int(self._task_cursors.get(str(task_id), 0))

    def set_task_cursor(self, task_id: str, seq: int) -> None:
        with self._lock:
            self._ensure_loaded()
            self._task_cursors[str(task_id)] = int(seq)
            self._dirty = True
            self._flush_locked()

    # ── 读 ──────────────────────────────────

    def resolve(self, item: Item) -> Any:
        if item.content_ref:
            try:
                raw = json.loads((self.dir / item.content_ref).read_text(encoding="utf-8"))
                return raw.get("content")
            except Exception:  # noqa: BLE001 — 外置文件丢了就退回内联
                return item.content_inline
        return item.content_inline

    def get(self, seq: int, *, resolve_content: bool = True) -> Optional[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(int(seq))
            if item is None:
                return None
            payload = item.to_dict()
            payload["content"] = self.resolve(item) if resolve_content else None
            return payload

    def list_items(
        self, *, since_seq: int = 0, limit: int = 200,
        resolve_content: bool = True, until_seq: int = 0, before_seq: int = 0,
    ) -> dict[str, Any]:
        """按 seq 升序取一段（刷新/分页用）。

        - `since_seq` / `until_seq`：向后翻（`since < seq <= until`）；
        - `before_seq`：**向前翻**（`seq < before`，取最后 `limit` 条）——聊天窗口
          "加载更早"只需要这条路径，不能让前端自己算偏移量。
        """
        lim = max(1, min(int(limit or 200), MAX_PAGE))
        with self._lock:
            self._ensure_loaded()
            seqs = [s for s in sorted(self._items) if s > int(since_seq)]
            if until_seq:
                seqs = [s for s in seqs if s <= int(until_seq)]
            has_more = False
            if before_seq:
                older = [s for s in seqs if s < int(before_seq)]
                has_more = len(older) > lim
                page = older[-lim:]
            else:
                has_more = len(seqs) > lim
                page = seqs[:lim]
            items = []
            for seq in page:
                item = self._items[seq]
                payload = item.to_dict()
                payload["content"] = self.resolve(item) if resolve_content else None
                items.append(payload)
            latest = max(self._items) if self._items else 0
            return {
                "items": items,
                "has_more": has_more,
                "latest_seq": latest,
                "next_seq": self._next_seq - 1,
                "archived_until_seq": self._archived_until,
                "counters": dict(self._counters),
                # 绑定到本会话的任务（按绑定顺序）。前端用它把「轨迹」固定标签接上
                # 当前任务——刷新后不需要再从内存里猜。
                "task_ids": list(self._task_ids),
            }

    def tail(self, limit: int = 200) -> dict[str, Any]:
        """最后 N 条（`since_seq` 语义不方便时用；返回仍按 seq 升序）。"""
        with self._lock:
            self._ensure_loaded()
            seqs = sorted(self._items)[-max(1, min(int(limit or 200), MAX_PAGE)):]
            start = (seqs[0] - 1) if seqs else 0
        return self.list_items(since_seq=start, limit=limit)

    def latest_seq(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return max(self._items) if self._items else 0

    def user_turns(self) -> int:
        """已有多少轮用户发言（用于给条目打 `turn`）。"""
        with self._lock:
            self._ensure_loaded()
            return sum(
                1 for item in self._items.values()
                if item.type == "content" and (item.meta or {}).get("role") == "user"
            )

    def find_by_ref(self, ref: str) -> Optional[Item]:
        """按 `ref` 幂等查找（task 事件投影用：重复 append 视为 patch）。"""
        if not ref:
            return None
        with self._lock:
            self._ensure_loaded()
            for item in self._items.values():
                if item.ref == ref:
                    return item
        return None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return self._snapshot()

    def close_open(self, state: str = "interrupted") -> int:
        """把所有还开着的流式条目收尾（异常/断连时用，避免 UI 永远转圈）。"""
        if state not in STATES:
            state = "interrupted"
        with self._lock:
            self._ensure_loaded()
            targets = [seq for seq, item in self._items.items() if item.state == "streaming"]
        for seq in targets:
            try:
                self.finish(seq, state=state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("收尾流式条目失败 %s seq=%s: %s", self.session_id, seq, exc)
        return len(targets)

    def flush(self) -> None:
        """强制落盘（收尾/关闭时用）。"""
        with self._lock:
            self._ensure_loaded()
            self._dirty = True
            self._flush_locked(force=True)


class Turn:
    """一轮对话的写助手。

    解决的问题：流式条目的 seq 必须在**第一条增量到达时**就分配（先占位），
    但调用方（`RequirementsService.chat`）在写代码时还不知道模型会不会回吐思考。
    所以这里做懒开：`stream("thinking", delta)` 第一次被调用才 `begin`，
    之后都往同一个 seq 上追加；`finish` 用**权威全文**覆盖，避免丢块拼接出残缺文本。
    """

    def __init__(self, log: SessionLog, *, turn: Optional[int] = None,
                 source: str = "requirements") -> None:
        self.log = log
        self.turn = turn
        self.source = source
        self._open: dict[str, int] = {}

    def open_seq(self, type: str) -> Optional[int]:
        return self._open.get(type)

    def stream(self, type: str, delta: str) -> Optional[int]:
        """懒开 + 追加增量。返回该条目的 seq。"""
        if not delta:
            return self._open.get(type)
        seq = self._open.get(type)
        if seq is None:
            item = self.log.begin(type=type, source=self.source, turn=self.turn)
            seq = item.seq
            self._open[type] = seq
        self.log.chunk(seq, delta)
        return seq

    def finish(self, type: str, *, content: Any = None, state: str = "done",
               meta: Optional[dict[str, Any]] = None) -> Optional[int]:
        """收尾：有占位就用权威内容完成它，没占位且给了内容才新建一条。"""
        seq = self._open.pop(type, None)
        if seq is not None:
            self.log.finish(seq, content=content, state=state, meta=meta)
            return seq
        if content is None:
            return None
        item = self.log.append(type=type, content=content, source=self.source,
                               turn=self.turn, state=state, meta=meta)
        return item.seq

    def append(self, type: str, content: Any, *, meta: Optional[dict[str, Any]] = None,
               ref: Optional[str] = None, state: str = "done") -> int:
        item = self.log.append(type=type, content=content, source=self.source,
                               turn=self.turn, meta=meta, ref=ref, state=state)
        return item.seq

    def close(self, *, state: str = "done") -> None:
        """把还开着的流式条目收尾（异常路径：内容保留已 flush 的部分）。"""
        for type_, seq in list(self._open.items()):
            self._open.pop(type_, None)
            self.log.finish(seq, state=state)


# ── 注册表 ───────────────────────────────────

def get_log(session_id: str, *, project_id: str = "") -> SessionLog:
    """按会话 id 取（首次访问时载入并做崩溃恢复）。"""
    key = _safe_id(session_id)
    with _REGISTRY_LOCK:
        log = _LOGS.get(key)
        if log is None:
            log = SessionLog(session_id, project_id=project_id)
            _LOGS[key] = log
        elif project_id:
            log.set_project(project_id)
        return log


def has_log(session_id: str) -> bool:
    return _safe_id(session_id) in _LOGS


def flush_all() -> None:
    for log in list(_LOGS.values()):
        try:
            log.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话落盘失败 %s: %s", log.session_id, exc)


def reset(session_id: str = "", *, drop_files: bool = False) -> None:
    """清内存登记（测试/重置用）。`drop_files=True` 连目录一起删。"""
    with _REGISTRY_LOCK:
        keys = [_safe_id(session_id)] if session_id else list(_LOGS)
        for key in keys:
            log = _LOGS.pop(key, None)
            if log is not None and drop_files:
                import shutil

                shutil.rmtree(log.dir, ignore_errors=True)


def iter_session_dirs() -> Iterable[Path]:
    """已落盘的会话目录（排查/清理用）。"""
    root = sessions_root()
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "session.json").exists():
            yield path
