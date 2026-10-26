"""任务黑板：阶段之间的数据交换台（PRD docs/master-agent-prd-2026-10-26.md §3.2）。

**为什么需要它**：管线时代靠 `PipelineState.shared_data` 隐式串数据——输入装配写在
`pipeline_v2._dispatch` 里、合并语义写在 `_merge_agent_result` 里，主 Agent 完全看不到。
主 Agent 要派阶段子代理，就必须有一份"显式的、带归属的、可校验的"共享数据。

三条纪律：

1. **每次写入都记 `by`（哪个阶段写的）** —— 时间线被谁改坏必须能查；
2. **`timeline` 只允许下游覆盖上游**（edit → animation → audio）—— 倒序写会让
   剪辑把动画/音频的产物覆盖成"没字幕没配音"的粗剪；
3. **大对象不进内存**：超过 `MAX_BLACKBOARD_BYTES` 的值只存指针（工作区文件 + sha256），
   否则一条 4K 时间线能把进程内存打爆。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 单个值的字节上限（超出则要求调用方给文件指针）
MAX_BLACKBOARD_BYTES = 2 * 1024 * 1024

#: 时间线的生产者顺序：只允许**序号变大**的方向写（下游覆盖上游）
TIMELINE_STAGE_ORDER: dict[str, int] = {"edit": 1, "animation": 2, "audio": 3}

#: 各阶段"会写哪些键"（供主 Agent 与审计使用，契约见 PRD §3.2）
STAGE_WRITES: dict[str, tuple[str, ...]] = {
    "structure": ("script_skeleton", "scenes"),
    "material": ("candidate_clips",),
    "edit": ("timeline",),
    "animation": ("timeline", "animation_plan"),
    "audio": ("timeline", "final_timeline"),
    "quality": ("issues", "redo_agent"),
}

_BOARDS: dict[str, dict[str, Any]] = {}
_OWNERS: dict[str, dict[str, dict[str, Any]]] = {}


class BlackboardError(RuntimeError):
    """写入被拒绝（倒序写时间线 / 值过大 / 类型非法）。"""


def _size_of(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001 — 序列化失败按超限处理，宁可要指针
        return MAX_BLACKBOARD_BYTES + 1


def get(task_id: str) -> dict[str, Any]:
    """取整个黑板（浅拷贝，避免调用方直接改内部字典）。"""
    return dict(_BOARDS.get(str(task_id), {}))


def snapshot(task_id: str, keys: Iterable[str] | None = None) -> dict[str, Any]:
    board = _BOARDS.get(str(task_id), {})
    if keys is None:
        return dict(board)
    return {k: board[k] for k in keys if k in board}


def owner_of(task_id: str, key: str) -> str:
    return str((_OWNERS.get(str(task_id), {}).get(key) or {}).get("by") or "")


def describe(task_id: str) -> dict[str, Any]:
    board = _BOARDS.get(str(task_id), {})
    owners = _OWNERS.get(str(task_id), {})
    return {
        "task_id": str(task_id),
        "keys": sorted(board.keys()),
        "total_bytes": sum(_size_of(v) for v in board.values()),
        "by": {k: (owners.get(k) or {}).get("by", "") for k in board},
    }


def put(task_id: str, key: str, value: Any, *, by: str = "") -> None:
    """写一个键。`timeline` 走单向覆盖校验；超大值只接受文件指针形状。"""
    tid, k = str(task_id), str(key)
    if not k:
        raise BlackboardError("key 不能为空")

    if k == "timeline":
        # 空 by 也必须拒绝：`get("", 99)` 恒 >= 任何真实阶段的 rank，
        # 「匿名写」等于把单向覆盖校验整个绕过（实测可覆盖 audio 定稿）。
        if by not in TIMELINE_STAGE_ORDER:
            raise BlackboardError(
                f"{by or '匿名'} 不是时间线生产者"
                f"（只允许 {'/'.join(TIMELINE_STAGE_ORDER)} 写 timeline）"
            )
        current = owner_of(tid, k)
        new_rank = TIMELINE_STAGE_ORDER.get(by or "", 99)
        old_rank = TIMELINE_STAGE_ORDER.get(current or "", -1)
        if current and new_rank < old_rank:
            raise BlackboardError(
                f"时间线只能由下游覆盖上游：{by or '未知阶段'} 不能覆盖 {current} 写的时间线"
                "（否则动画/音频产物会被粗剪覆盖，成片会突然没字幕没配音）"
            )

    size = _size_of(value)
    if size > MAX_BLACKBOARD_BYTES and not (
        isinstance(value, dict) and str(value.get("path") or "").strip()
    ):
        raise BlackboardError(
            f"黑板值过大（{size / 1024 / 1024:.1f}MB > {MAX_BLACKBOARD_BYTES / 1024 / 1024:.0f}MB）："
            f"{k} 请改成文件指针 {{path, sha256}}"
        )

    board = _BOARDS.setdefault(tid, {})
    previous = owner_of(tid, k)
    board[k] = value
    _OWNERS.setdefault(tid, {})[k] = {
        "by": str(by or ""),
        "ts": time.time(),
        "bytes": size,
        "previous_by": previous,
    }
    if previous and previous != by:
        logger.info("黑板覆盖 %s.%s：%s → %s", tid, k, previous, by or "未知")


def merge(task_id: str, data: dict[str, Any], *, by: str = "") -> list[str]:
    """批量写入，返回成功写入的键；单键失败抛 `BlackboardError`（不静默跳过）。"""
    written: list[str] = []
    for key, value in (data or {}).items():
        if value is None:
            continue
        put(task_id, str(key), value, by=by)
        written.append(str(key))
    return written


def reset(task_id: str = "") -> None:
    if task_id:
        _BOARDS.pop(str(task_id), None)
        _OWNERS.pop(str(task_id), None)
    else:
        _BOARDS.clear()
        _OWNERS.clear()


def stages_that_can_write(stage: str) -> tuple[str, ...]:
    return STAGE_WRITES.get(str(stage or ""), ())


__all__ = [
    "MAX_BLACKBOARD_BYTES",
    "STAGE_WRITES",
    "TIMELINE_STAGE_ORDER",
    "BlackboardError",
    "describe",
    "get",
    "merge",
    "owner_of",
    "put",
    "reset",
    "snapshot",
    "stages_that_can_write",
]
