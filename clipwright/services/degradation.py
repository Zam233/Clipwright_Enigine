"""降级登记（F9 / PRD v0.2 §F9）。

背景：`llm_or_fallback`、结构化场景的 fallback、类型 transform 失败这类路径
过去只打一行 ``logger.warning`` 就继续，decision 仍然是 PASS —— 编排层和质检
都看不到，于是「用占位图顶上的段落」被当成正常成片交付。

约定（单点埋设，不在 6 个 Agent 里各写一份）：
- 编排层在调用 Agent 前用 ``degradation_scope(sink)`` 包一层；
- Agent 内部任何位置调用 ``note_degradation(...)``；
- 作用域退出后由调用方把 sink 并入输出的 ``degradations`` 字段。

没有活跃作用域时只记 warning 日志（便于发现漏接线的调用点），不静默丢弃。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from clipwright.config import logger
from clipwright.schema.agent import Degradation

#: 当前活跃的降级收集器（asyncio/线程上下文隔离）
_ACTIVE: ContextVar[list[Degradation] | None] = ContextVar(
    "agent_degradation_sink", default=None
)


def note_degradation(
    kind: str,
    detail: str = "",
    scope: str = "",
    impact: str = "",
) -> Degradation:
    """登记一次降级。返回登记项（便于知情方继续追加信息）。"""
    item = Degradation(kind=kind, detail=detail, scope=scope, impact=impact)
    sink = _ACTIVE.get()
    if sink is None:
        logger.warning(
            "降级发生在无登记作用域内（归因将丢失）: kind=%s scope=%s detail=%.120s",
            kind, scope, detail,
        )
        return item
    sink.append(item)
    logger.info("降级登记: kind=%s scope=%s detail=%.120s", kind, scope, detail)
    return item


@contextmanager
def degradation_scope(sink: list[Degradation] | None = None) -> Iterator[list[Degradation]]:
    """打开降级收集作用域；退出时恢复上一层的收集器。"""
    target: list[Degradation] = sink if sink is not None else []
    token = _ACTIVE.set(target)
    try:
        yield target
    finally:
        _ACTIVE.reset(token)


def current_degradations() -> list[Degradation]:
    """只读快照（诊断/日志用）。"""
    return list(_ACTIVE.get() or [])
