"""任务结果快照（modern-agent PRD §4.1：从 `pipeline_capability` 迁出的中立模块）。

为什么要单独一个模块：`last_result()` 被 4 个地方读（api/task、api/pipeline 的历史只读、
golden_capture），但它跟"跑老管线"没有关系——一次 tool loop 任务同样需要一个
"这个任务最后产出了什么"的只读快照。把它留在 `pipeline_capability` 里，等于让
"读结果"必须依赖"能执行老管线"，于是删不掉老管线。

约定：
- **只读快照**：这里只存"结果是什么"，不含执行能力；
- 有界：内存最多保留 `_MAX_KEEP` 个任务的结果；
- 不参与判定：CompletionGate 看黑板里的 `final_timeline`，不看这里。
"""

from __future__ import annotations

from typing import Any, Optional

#: 任务 → 最近一次结果快照
_LAST_RESULT: dict[str, dict[str, Any]] = {}
_MAX_KEEP = 50


def remember(task_id: str, result: dict[str, Any]) -> None:
    """写一条结果快照（有界）。"""
    if not task_id:
        return
    _LAST_RESULT[str(task_id)] = dict(result or {})
    while len(_LAST_RESULT) > _MAX_KEEP:
        _LAST_RESULT.pop(next(iter(_LAST_RESULT)), None)


def last_result(task_id: str) -> Optional[dict[str, Any]]:
    """最近一次结果快照：**优先生成于黑板**（时间线的唯一事实来源）。

    M3 之前这份快照由 `pipeline_capability.run` 顺手写入；现在主 loop 是 tool loop，
    时间线由领域层写进黑板（`final_timeline`）。所以这里先看有没有显式快照，
    没有再回落到黑板——而不是让"读结果"依赖"能跑老管线"。
    """
    key = str(task_id)
    snapshot = _LAST_RESULT.get(key)
    if snapshot:
        return snapshot
    try:
        from clipwright.services import task_blackboard as _bb

        board = _bb.get(key)
    except Exception:  # noqa: BLE001 — 黑板不可用就当真没有
        return None
    timeline = board.get("final_timeline") or board.get("timeline")
    if not timeline:
        return None
    return {"task_id": key, "timeline": timeline, "status": "completed",
            "source": "blackboard"}


def clear_results() -> None:
    _LAST_RESULT.clear()


__all__ = ["clear_results", "last_result", "remember"]
