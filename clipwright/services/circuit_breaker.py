"""Agent 级熔断表（modern-agent PRD §4.4：「预算 reserve/settle 与熔断」属保留不变量）。

原来它长在 `PipelineOrchestratorV2` 上（类属性），于是 `/breaker-status` 端点
必须 import 编排器才能读到一张表。M3 之后编排器不存在了，但这张表本身仍然有用：
LLM provider 故障是**进程级事实**，一个阶段连续 429，其它阶段不该继续往上撞。

所以把它抽成独立注册表，并接上新的执行路径（`agent_run` 每次 run 结束记一笔），
`/breaker-status` 直接读快照——不再需要任何执行器在场。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from clipwright.config import logger

#: agent/阶段名 → {fail_count, last_fail_at}
_BREAKERS: dict[str, dict[str, Any]] = {}

#: 连续失败多少次算熔断；熔断后多久自动恢复
THRESHOLD = 3
RECOVERY_SEC = 60


def reset() -> None:
    _BREAKERS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def key_of(stage: str, model: str = "") -> str:
    """熔断键：无模型时就是阶段名；带模型时 `stage::model`。

    为什么按 (阶段, 模型) 分键：一个 provider 挂了不代表同阶段所有模型都挂。
    按阶段单键会把"某个模型不可用"升级成"这个阶段不许跑"，
    而按模型分键才能做到"换一个模型继续"。
    """
    name = str(stage or "").strip()
    model_key = str(model or "").strip()
    return f"{name}::{model_key}" if model_key else name


def failed_models_for(stage: str) -> list[str]:
    """该阶段里处于熔断状态的**模型**列表（供路由换模型用）。"""
    prefix = f"{str(stage or '').strip()}::"
    out: list[str] = []
    for key, cb in _BREAKERS.items():
        if not key.startswith(prefix):
            continue
        if int(cb.get("fail_count", 0)) >= THRESHOLD:
            out.append(key[len(prefix):])
    return sorted(out)


def is_open(key: str) -> bool:
    """该 agent/阶段是否处于熔断中（过了恢复期会自动复位）。"""
    cb = _BREAKERS.get(str(key))
    if not cb:
        return False
    if int(cb.get("fail_count", 0)) < THRESHOLD:
        return False
    last = cb.get("last_fail_at")
    if isinstance(last, datetime):
        elapsed = (_now() - last).total_seconds()
        if elapsed > RECOVERY_SEC:
            cb["fail_count"] = 0
            logger.info("熔断恢复: %s（已过恢复期 %.0fs）", key, elapsed)
            return False
    logger.warning("Agent 熔断: %s（连续失败 %s 次）", key, cb.get("fail_count"))
    return True


def record_failure(key: str) -> None:
    """记一次失败（集中在一处，避免各调用点各写一份计数）。"""
    name = str(key or "").strip()
    if not name:
        return
    cb = _BREAKERS.setdefault(name, {"fail_count": 0, "last_fail_at": None})
    cb["fail_count"] = int(cb.get("fail_count", 0)) + 1
    cb["last_fail_at"] = _now()
    if cb["fail_count"] >= THRESHOLD:
        logger.warning("Agent 熔断触发: %s（连续失败 %d/%d 次）",
                       name, cb["fail_count"], THRESHOLD)


def record_success(key: str) -> None:
    """记一次成功：计数归零（保留 last_fail_at 供恢复期展示）。"""
    name = str(key or "").strip()
    if not name:
        return
    cb = _BREAKERS.get(name)
    if not cb:
        return
    if int(cb.get("fail_count", 0)) > 0:
        logger.info("Agent 熔断重置: %s", name)
    cb["fail_count"] = 0


def snapshot() -> dict[str, Any]:
    """熔断健康快照（`/breaker-status` 的数据源）。"""
    now = _now()
    agents: list[dict[str, Any]] = []
    tripped = 0
    for name, cb in sorted(_BREAKERS.items()):
        count = int(cb.get("fail_count", 0))
        last = cb.get("last_fail_at")
        elapsed = (now - last).total_seconds() if isinstance(last, datetime) else None
        is_open = count >= THRESHOLD and (elapsed is None or elapsed <= RECOVERY_SEC)
        if is_open:
            tripped += 1
        agents.append({
            "agent": name,
            "fail_count": count,
            "threshold": THRESHOLD,
            "open": is_open,
            "recovery_sec": RECOVERY_SEC,
            "seconds_since_fail": round(elapsed, 1) if elapsed is not None else None,
        })
    return {
        "status": "degraded" if tripped > 0 else "ok",
        "tripped_agents": tripped,
        "agents": agents,
    }


__all__ = ["RECOVERY_SEC", "THRESHOLD", "failed_models_for", "is_open", "key_of",
           "record_failure", "record_success", "reset", "snapshot"]
