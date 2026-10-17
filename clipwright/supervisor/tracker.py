"""调度中枢：子代理追踪（PRD F12 / 开发计划 B7）。

职责：只做**观测与控制**，不负责派生（派生在 B8 的 `spawn.py`）。

- 内存注册表按 pipeline 归属，有界（默认 200 条管线）；
- 状态机：pending → running → done / failed / cancelled；
- `cancel()` 只改状态并回调（真正的取消动作由 B8 注入 `cancel_cb`）；
- 每次状态变化写 trace（`subagent_start / subagent_progress / subagent_end`），
  前端复用现有 SSE 流按 type 分流。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from clipwright.config import logger
from clipwright.schema.supervisor import SubagentNode

_MAX_TRACKERS = 200
_TERMINAL = frozenset({"done", "failed", "cancelled"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubagentTracker:
    """一条管线的子代理树。"""

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self._nodes: dict[str, SubagentNode] = {}
        self._cancel_cbs: dict[str, Callable[[], None]] = {}

    # ── 写入 ────────────────────────────────────────────────

    def register(
        self,
        role: str,
        *,
        kind: str = "ephemeral",
        parent_id: Optional[str] = None,
        provider: str = "",
        model: str = "",
        cancellable: bool = True,
        cancel_cb: Optional[Callable[[], None]] = None,
    ) -> SubagentNode:
        """登记一个子代理（返回节点；调用方据此落 id 给前端）。"""
        node = SubagentNode(
            id=f"sa_{uuid4().hex[:8]}",
            parent_id=parent_id,
            role=role,
            kind=kind,
            model={"provider": provider, "model": model} if (provider or model) else {},
            cancellable=cancellable,
        )
        self._nodes[node.id] = node
        if cancel_cb is not None:
            self._cancel_cbs[node.id] = cancel_cb
        self._event("subagent_start", node, "子代理登记")
        return node

    def update(self, subagent_id: str, **fields: Any) -> Optional[SubagentNode]:
        """更新节点字段（status/last_output/model/duration_ms/cost_usd）。"""
        node = self._nodes.get(subagent_id)
        if node is None:
            return None
        allowed = {"status", "last_output", "model", "duration_ms", "cost_usd", "cancellable"}
        changed = False
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(node, key, value)
                changed = True
        if not changed:
            return node
        node.ts = _utcnow()
        if node.status == "running":
            self._event("subagent_progress", node, node.last_output or "运行中")
        return node

    def finish(self, subagent_id: str, *, status: str = "done",
               last_output: str = "", duration_ms: int = 0) -> Optional[SubagentNode]:
        """结束节点（done/failed/cancelled）。"""
        node = self._nodes.get(subagent_id)
        if node is None:
            return None
        node.status = status if status in _TERMINAL else "done"
        if last_output:
            node.last_output = last_output[:500]
        node.duration_ms = max(0, int(duration_ms or node.duration_ms))
        node.ts = _utcnow()
        self._cancel_cbs.pop(subagent_id, None)
        self._event("subagent_end", node, f"子代理结束: {node.status}")
        return node

    def cancel(self, subagent_id: str) -> bool:
        """取消一个子代理；不可取消/已结束返回 False。"""
        node = self._nodes.get(subagent_id)
        if node is None or not node.cancellable or node.status in _TERMINAL:
            return False
        node.status = "cancelled"
        node.ts = _utcnow()
        cb = self._cancel_cbs.pop(subagent_id, None)
        if cb is not None:
            try:
                cb()
            except Exception as e:  # noqa: BLE001 — 取消回调失败不影响状态
                logger.warning("子代理取消回调失败 %s: %s", subagent_id, e)
        self._event("subagent_end", node, "子代理已取消")
        return True

    # ── 读取 ────────────────────────────────────────────────

    def get(self, subagent_id: str) -> Optional[SubagentNode]:
        return self._nodes.get(subagent_id)

    def list_nodes(self) -> list[SubagentNode]:
        return list(self._nodes.values())

    def tree(self) -> list[dict[str, Any]]:
        """树形输出（父在前，子嵌套在 children 里）。"""
        children: dict[Optional[str], list[SubagentNode]] = {}
        for node in self._nodes.values():
            children.setdefault(node.parent_id, []).append(node)

        def _build(parent: Optional[str], depth: int = 0) -> list[dict[str, Any]]:
            if depth > 8:  # 防环/防深递归（星型拓扑本不该出现）
                return []
            out: list[dict[str, Any]] = []
            for node in children.get(parent, []):
                item = node.model_dump(mode="json")
                item["children"] = _build(node.id, depth + 1)
                out.append(item)
            return out

        return _build(None)

    def snapshot(self) -> dict[str, Any]:
        nodes = list(self._nodes.values())
        by_status: dict[str, int] = {}
        for n in nodes:
            by_status[n.status] = by_status.get(n.status, 0) + 1
        return {
            "pipeline_id": self.pipeline_id,
            "count": len(nodes),
            "by_status": by_status,
            "nodes": [n.model_dump(mode="json") for n in nodes],
            "tree": self.tree(),
        }

    def _event(self, event_type: str, node: SubagentNode, summary: str) -> None:
        try:
            from clipwright.services.trace import add_event

            add_event(
                self.pipeline_id, "supervisor", event_type, summary,
                {"subagent_id": node.id, "role": node.role, "status": node.status},
                parent_agent="supervisor", depth=1,
            )
        except Exception as e:  # noqa: BLE001 — 埋点失败不影响执行
            logger.debug("子代理事件写入失败: %s", e)


# ── 注册表（按 pipeline 归属，有界）───────────────────────────

_TRACKERS: dict[str, SubagentTracker] = {}


def get_tracker(pipeline_id: str) -> SubagentTracker:
    tracker = _TRACKERS.get(pipeline_id)
    if tracker is None:
        if len(_TRACKERS) >= _MAX_TRACKERS:
            oldest = next(iter(_TRACKERS))
            _TRACKERS.pop(oldest, None)
        tracker = SubagentTracker(pipeline_id)
        _TRACKERS[pipeline_id] = tracker
    return tracker


def drop_tracker(pipeline_id: str) -> None:
    _TRACKERS.pop(pipeline_id, None)


def clear_trackers() -> None:
    _TRACKERS.clear()


def tracker_count() -> int:
    return len(_TRACKERS)
