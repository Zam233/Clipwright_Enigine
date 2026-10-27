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


__all__ = ["clear_results", "last_result", "quality_summary",
           "remember", "remember_from_board"]

def quality_summary(board: dict[str, Any] | None = None) -> dict[str, Any]:
    """把黑板里的质检 issues 压成可展示的摘要（Task 路径的质检口径）。

    为什么放在这里：老管线在结果字典里塞 `quality_issues`，Task 路径没有等价物，
    前端质检 Tab 于是永远空。这里读**黑板**（`issues` / `redo_agent`）而不是
    "某次 run 的返回值"——质检是任务级事实，不是某一步的副产品。
    """
    board = dict(board or {})
    issues = [i for i in (board.get("issues") or []) if isinstance(i, dict)]
    redo = str(board.get("redo_agent") or "")
    if not issues and not redo:
        return {}

    def _severity(item: dict[str, Any]) -> str:
        return str(item.get("severity") or item.get("level") or "warn")

    by_sev: dict[str, int] = {}
    for item in issues:
        by_sev[_severity(item)] = by_sev.get(_severity(item), 0) + 1
    return {
        "count": len(issues),
        "by_severity": by_sev,
        "redo_agent": redo,
        "items": [
            {"kind": str(i.get("kind") or i.get("type") or ""),
             "severity": _severity(i),
             "detail": str(i.get("detail") or i.get("message") or "")[:200],
             "clip_id": str(i.get("clip_id") or "")}
            for i in issues[:20]
        ],
    }


def remember_from_board(task_id: str, board: dict[str, Any] | None = None) -> None:
    """任务收尾时用黑板写一份结果快照（含质检摘要）。"""
    board = dict(board or {})
    timeline = board.get("final_timeline") or board.get("timeline")
    snapshot: dict[str, Any] = {"task_id": str(task_id), "source": "blackboard"}
    if timeline:
        snapshot["timeline"] = timeline
        snapshot["status"] = "completed"
    quality = quality_summary(board)
    if quality:
        snapshot["quality_issues"] = quality
    if snapshot.get("timeline") or quality:
        remember(str(task_id), snapshot)

