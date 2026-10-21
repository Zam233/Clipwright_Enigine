"""EpisodeLedger —— 管线账本（PRD F1 / 开发计划 B1）。

设计要点（与计划 §4.2 对齐）：

1. **单点埋设**：只有 `pipeline_v2._run_agent` 一处调用 `arecord`，6 个 Agent
   内部零改动；降级来自 B0 的 `degradations` 字段，失败来自 F9 的 `failure`。
2. **进程内 + Mongo**：内存保证同步读取（API/诊断书立刻可用），Mongo 异步落库
   供跨进程诊断；未连库时静默降级为纯内存，不影响管线。
3. **给决策方的摘要**：`summary()` 输出紧凑文本（不是原始日志），供 B2 的归因
   与 B3 的策略选择消费，超长截断并标注。
4. **有界**：注册表最多保留 `_MAX_LEDGERS` 条管线，超出按最久未写入淘汰，
   避免长跑进程里无限增长。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import uuid4

from clipwright.config import logger
from clipwright.schema.agent import Degradation, FailureReport
from clipwright.schema.supervisor import LedgerEntry

_COLLECTION = "pipeline_ledger"
_MAX_LEDGERS = 200


class EpisodeLedger:
    """一条管线的账本（内存为主，Mongo 为旁路持久化）。"""

    def __init__(self, pipeline_id: str, *, persist: bool = True) -> None:
        self.pipeline_id = pipeline_id
        self._persist = persist
        self._entries: list[LedgerEntry] = []

    # ── 写入 ────────────────────────────────────────────────

    def record(
        self,
        *,
        stage: str,
        decision: str = "pass",
        failure: FailureReport | dict[str, Any] | None = None,
        degradations: Iterable[Degradation | dict[str, Any]] | None = None,
        warnings: Iterable[str] | None = None,
        model: str = "",
        usage: Optional[dict[str, Any]] = None,
        duration_ms: int = 0,
        input_digest: str = "",
    ) -> LedgerEntry:
        """记录一次阶段尝试（同步；测试与同步路径用）。"""
        entry = LedgerEntry(
            id=f"le_{uuid4().hex[:10]}",
            stage=stage,
            attempt=self.attempts(stage) + 1,
            decision=decision,
            input_digest=input_digest[:500],
            failure=_as_failure(failure),
            degradations=[_as_degradation(d) for d in (degradations or [])],
            warnings=[str(w)[:200] for w in (warnings or [])][:20],
            model=model or "",
            usage=dict(usage or {}),
            duration_ms=int(duration_ms or 0),
        )
        self._entries.append(entry)
        if self._persist:
            _persist_entry(self.pipeline_id, entry)
        return entry

    async def arecord(self, **kwargs: Any) -> LedgerEntry:
        """记录一次阶段尝试（异步；把 Mongo 写放到线程池，不阻塞事件循环）。"""
        entry = self.record(**kwargs)
        if self._persist and _mongo_available():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _persist_entry, self.pipeline_id, entry)
            except Exception as e:  # noqa: BLE001 — 落库失败不得影响管线
                logger.warning("账本落库失败（保留内存副本）: %s", e)
        return entry

    # ── 读取 ────────────────────────────────────────────────

    def entries(self, stage: str = "") -> list[LedgerEntry]:
        if not stage:
            return list(self._entries)
        return [e for e in self._entries if e.stage == stage]

    def attempts(self, stage: str) -> int:
        return sum(1 for e in self._entries if e.stage == stage)

    def degradations(self) -> list[Degradation]:
        out: list[Degradation] = []
        for e in self._entries:
            out.extend(e.degradations)
        return out

    def failures(self) -> list[LedgerEntry]:
        return [e for e in self._entries if e.failure is not None]

    def to_public(self) -> dict[str, Any]:
        """API 序列化：条目 + 每阶段尝试数（前端与诊断书用）。"""
        attempts: dict[str, int] = {}
        for e in self._entries:
            attempts[e.stage] = max(attempts.get(e.stage, 0), e.attempt)
        return {
            "pipeline_id": self.pipeline_id,
            "found": bool(self._entries),
            "count": len(self._entries),
            "attempts": attempts,
            "entries": [e.model_dump(mode="json") for e in self._entries],
        }

    def summary(self, max_chars: int = 4000) -> str:
        """给决策方的紧凑摘要（PRD F1：≤4000 字，可裁剪）。"""
        if not self._entries:
            return "（账本为空：该管线尚未执行任何阶段）"

        lines: list[str] = [f"# 管线 {self.pipeline_id} 账本摘要"]
        for e in self._entries:
            marks: list[str] = []
            if e.failure is not None:
                marks.append("失败")
            if e.degradations:
                marks.append(f"降级×{len(e.degradations)}")
            if e.warnings:
                marks.append(f"警告×{len(e.warnings)}")
            head = f"- [{e.stage} 第{e.attempt}次] {e.decision}"
            if marks:
                head += "（" + "、".join(marks) + "）"
            if e.duration_ms:
                head += f" {e.duration_ms}ms"
            if e.model:
                head += f" model={e.model}"
            lines.append(head)

            if e.failure is not None:
                f = e.failure
                detail = [f"category={f.category}"]
                if f.blocking_point:
                    detail.append(f"阻塞={f.blocking_point}")
                if f.retryable and f.retryable != "retry_same":
                    detail.append(f"可重试性={f.retryable}")
                for need in f.needs[:3]:
                    detail.append(
                        "需要上游="
                        + "/".join(str(need.get(k, "")) for k in ("from_stage", "field") if need.get(k))
                    )
                lines.append("  " + "｜".join(detail))

            for d in e.degradations[:5]:
                scope = f"{d.scope}: " if d.scope else ""
                impact = f"（影响：{d.impact}）" if d.impact else ""
                lines.append(f"  · 降级 {d.kind}: {scope}{d.detail}{impact}")

            for w in e.warnings[:3]:
                lines.append(f"  · 警告 {w}")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + f"\n…[已截断 {len(self._entries)} 条]"
        return text


# ── 注册表（按 pipeline_id 归属，有界）─────────────────────────

_LEDGERS: dict[str, EpisodeLedger] = {}


def get_ledger(pipeline_id: str, *, persist: bool = True) -> EpisodeLedger:
    """取（必要时创建）某管线的账本。"""
    ledger = _LEDGERS.get(pipeline_id)
    if ledger is None:
        if len(_LEDGERS) >= _MAX_LEDGERS:
            # 淘汰最早插入的一条（dict 保持插入序，管线完成后不再写入）
            oldest = next(iter(_LEDGERS))
            _LEDGERS.pop(oldest, None)
            logger.debug("EpisodeLedger: 淘汰最旧账本 %s", oldest)
        ledger = EpisodeLedger(pipeline_id, persist=persist)
        _LEDGERS[pipeline_id] = ledger
    return ledger


def drop_ledger(pipeline_id: str) -> None:
    """显式回收（测试与管线清理用）。"""
    _LEDGERS.pop(pipeline_id, None)


def clear_ledgers() -> None:
    """清空注册表（测试用）。"""
    _LEDGERS.clear()


def ledger_count() -> int:
    return len(_LEDGERS)


# ── 内部工具 ─────────────────────────────────────────────────


def _as_failure(value: FailureReport | dict[str, Any] | None) -> Optional[FailureReport]:
    if value is None:
        return None
    if isinstance(value, FailureReport):
        return value
    try:
        return FailureReport.model_validate(value)
    except Exception:  # noqa: BLE001 — 脏数据不应让账本写入失败
        return FailureReport(category="unknown", blocking_point=str(value)[:200])


def _as_degradation(value: Degradation | dict[str, Any]) -> Degradation:
    if isinstance(value, Degradation):
        return value
    try:
        return Degradation.model_validate(value)
    except Exception:  # noqa: BLE001
        return Degradation(kind="unknown", detail=str(value)[:200])


def _mongo_available() -> bool:
    try:
        from clipwright.context import mongo as mongo_ctx

        return bool(mongo_ctx.is_connected)
    except Exception:  # noqa: BLE001
        return False


def _persist_entry(pipeline_id: str, entry: LedgerEntry) -> None:
    """把一条账本条目 push 进 Mongo（best-effort，失败只告警）。"""
    try:
        from clipwright.context import mongo as mongo_ctx

        if not mongo_ctx.is_connected:
            return
        mongo_ctx.db[_COLLECTION].update_one(
            {"pipeline_id": pipeline_id},
            {
                "$push": {"entries": entry.model_dump(mode="json")},
                "$set": {
                    "pipeline_id": pipeline_id,
                    "updated_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("EpisodeLedger 落库失败 pipeline=%s: %s", pipeline_id, e)
