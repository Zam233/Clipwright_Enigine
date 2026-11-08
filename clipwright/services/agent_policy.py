"""终止策略解析：项目级覆盖 > 全局 settings > 代码默认（devplan 2026-11-02 §6/§7）。

为什么要单独一层：软着陆轮次、收口窗口、质量预算预留这三组数字，**作品级的判断**
（这条片子值不值得多跑两百轮）与部署级的默认值不是一回事。把它们写死在
`config.py` 里，等于逼运维替创作者拍板；写进 agent 里，又违反「判断交给模型/
策略、引擎只执行」的分工。所以这里只做一件事：把「项目配置 → 全局 → 默认」
解析成一份不可变快照，任务创建时取一次，运行中不再重读（否则跑一半被改配置
会变成两种真相）。

落点：项目 JSON 的 `agent_state.termination`（`ProjectManager` 的读-改-写锁内写）。
为什么不新建一套配置存储：`project.json` 已经有 `agent_state` 这个自由字段，
`ProjectManager.save()` 也已经带并发锁；再造一个集合只会多一份要对齐的真相。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from clipwright.config import settings

logger = logging.getLogger(__name__)

#: 项目 JSON 里挂终止策略的路径（`agent_state.termination`）
_AGENT_STATE_KEY = "agent_state"
_TERMINATION_KEY = "termination"

#: 允许项目级覆盖的键（其它键一律忽略：项目配置不是任意配置的入口）
_OVERRIDABLE: tuple[str, ...] = (
    "soft_landing_round",
    "soft_landing_enabled",
    "closeout_rounds",
    "closeout_tool_calls",
    "closeout_max_wall_sec",
    "reserve_ratio",
    "reserve_floor_usd",
    "reserve_cap_usd",
)

#: 兜底上下界（配置写错时不让它变成"永不收口"或"零额度"）
_BOUNDS: dict[str, tuple[float, float]] = {
    "soft_landing_round": (1, 10000),
    "closeout_rounds": (1, 64),
    "closeout_tool_calls": (0, 2000),
    "closeout_max_wall_sec": (30.0, 3600.0),
    "reserve_ratio": (0.0, 0.9),
    "reserve_floor_usd": (0.0, 100.0),
    "reserve_cap_usd": (0.0, 1000.0),
}


@dataclass(frozen=True)
class TerminationPolicy:
    """一份**快照**：任务创建时解析一次，之后不允许悄悄变。"""

    soft_landing_round: int = 600
    soft_landing_enabled: bool = True
    closeout_rounds: int = 8
    closeout_tool_calls: int = 128
    closeout_max_wall_sec: float = 600.0
    reserve_ratio: float = 0.2
    reserve_floor_usd: float = 0.05
    reserve_cap_usd: float = 2.0
    #: 快照来源：project / settings / default（写进日志与 task_end，便于排查"谁改的")
    source: str = "default"
    project_id: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "soft_landing_round": self.soft_landing_round,
            "soft_landing_enabled": self.soft_landing_enabled,
            "closeout_rounds": self.closeout_rounds,
            "closeout_tool_calls": self.closeout_tool_calls,
            "closeout_max_wall_sec": self.closeout_max_wall_sec,
            "reserve_ratio": self.reserve_ratio,
            "reserve_floor_usd": self.reserve_floor_usd,
            "reserve_cap_usd": self.reserve_cap_usd,
            "source": self.source,
            "project_id": self.project_id,
        }


def defaults_from_settings() -> TerminationPolicy:
    """全局 settings → 默认策略。"""
    return TerminationPolicy(
        soft_landing_round=int(getattr(settings, "agent_soft_landing_round", 600) or 600),
        soft_landing_enabled=bool(getattr(settings, "agent_soft_landing_enabled", True)),
        closeout_rounds=int(getattr(settings, "agent_closeout_rounds", 8) or 8),
        closeout_tool_calls=int(getattr(settings, "agent_closeout_tool_calls", 128) or 0),
        closeout_max_wall_sec=float(getattr(settings, "agent_closeout_max_wall_sec", 600.0) or 600.0),
        reserve_ratio=float(getattr(settings, "agent_quality_budget_reserve_ratio", 0.2)),
        reserve_floor_usd=float(getattr(settings, "agent_quality_budget_reserve_floor_usd", 0.05)),
        reserve_cap_usd=float(getattr(settings, "agent_quality_budget_reserve_cap_usd", 2.0)),
        source="settings",
    )


_INT_KEYS = frozenset({"soft_landing_round", "closeout_rounds", "closeout_tool_calls"})
_FLOAT_KEYS = frozenset({"closeout_max_wall_sec", "reserve_ratio",
                         "reserve_floor_usd", "reserve_cap_usd"})


def _clamp(key: str, value: Any) -> Any:
    """按 `_BOUNDS` 收口；类型不对就当没配（宁可用默认，也不让一条脏配置停掉整个任务）。"""
    low, high = _BOUNDS.get(key, (None, None))
    try:
        if key == "soft_landing_enabled":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if key in _INT_KEYS:
            number: Any = int(value)
        elif key in _FLOAT_KEYS:
            number = float(value)
        else:
            return None
        if low is not None:
            number = max(low, min(high, number))
        return number
    except (TypeError, ValueError):
        logger.warning("终止策略配置项非法，按默认处理: %s=%r", key, value)
        return None


def project_policy(project_id: str) -> dict[str, Any]:
    """读项目级终止策略（只返回白名单内的键）。读不到 → 空 dict。"""
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    try:
        from clipwright.services.project_manager import ProjectManager

        data = ProjectManager().load(pid) or {}
    except Exception as exc:  # noqa: BLE001 — 项目读失败不该让任务起不来
        logger.warning("读取项目终止策略失败 %s: %s", pid, exc)
        return {}
    state = data.get(_AGENT_STATE_KEY)
    if not isinstance(state, dict):
        return {}
    term = state.get(_TERMINATION_KEY)
    if not isinstance(term, dict):
        return {}
    return {k: v for k, v in term.items() if k in _OVERRIDABLE}


def set_project_policy(project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """写项目级终止策略（只改 `agent_state.termination`，保留 agent_state 其它键）。

    `patch` 里值为 None/"" 表示**删除**该键（回退全局）。
    """
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id 为空")
    cleaned: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        if key not in _OVERRIDABLE:
            raise KeyError(f"不可项目级覆盖的终止配置: {key}")
        cleaned[key] = value
    from clipwright.services.project_manager import ProjectManager

    manager = ProjectManager()
    with manager._save_lock:  # 复用既有读-改-写锁，避免并发丢更新
        data = manager.load(pid)
        if data is None:
            raise FileNotFoundError(f"项目不存在: {pid}")
        state = data.get(_AGENT_STATE_KEY)
        state = dict(state) if isinstance(state, dict) else {}
        term = state.get(_TERMINATION_KEY)
        term = dict(term) if isinstance(term, dict) else {}
        for key, value in cleaned.items():
            if value is None or value == "":
                term.pop(key, None)
            else:
                term[key] = value
        if term:
            state[_TERMINATION_KEY] = term
        else:
            state.pop(_TERMINATION_KEY, None)
        data[_AGENT_STATE_KEY] = state
        manager.save(pid, {_AGENT_STATE_KEY: state})
    logger.info("项目终止策略已更新 %s: %s", pid, cleaned)
    return project_policy(pid)


def resolve(project_id: str = "") -> TerminationPolicy:
    """解析终止策略快照：项目配置 > 全局 settings > 代码默认。"""
    policy = defaults_from_settings()
    pid = str(project_id or "").strip()
    raw = project_policy(pid) if pid else {}
    if not raw:
        return replace(policy, source="settings", project_id=pid)
    updates: dict[str, Any] = {}
    for key, value in raw.items():
        clamped = _clamp(key, value)
        if clamped is not None:
            updates[key] = clamped
    if not updates:
        return replace(policy, source="settings", project_id=pid)
    return replace(policy, **updates, source="project", project_id=pid)


def resolve_for_task(task: Any) -> TerminationPolicy:
    """按任务归属的项目解析（`project_id_of` 的取值顺序，含会话兜底）。"""
    try:
        from clipwright.services.task_project_sync import project_id_of

        pid = project_id_of(task)
    except Exception:  # noqa: BLE001 — 归属解析失败按全局策略走
        pid = ""
    return resolve(pid)


def build_escrow(budget_usd: float | None, policy: TerminationPolicy) -> Any:
    """按预算与策略冻结质量 agent 的预留额度（用户裁定 2）。

    规则（devplan §4.4 边界表）：
    - `budget_usd=null`（金额不限）→ `disabled`，不冻结、也**不凭空造上限**；
    - 预留 = clamp(budget × ratio, floor, cap)，且不得超过总预算的一半，
      避免小预算任务被自己的预留挤成零额度；
    - 冻结后 `main_budget_usd = budget - reserve`，主 agent 的金额闸只看这个数。

    为什么在这里而不是在闸门里扣：闸门每轮都会调用，临时减法会让"质量阶段还能
    不能跑"随主 agent 花钱漂移；冻结成快照后两边额度都是稳定的。
    """
    from clipwright.schema.task import BudgetEscrow, EscrowState, _utcnow

    if budget_usd is None or float(budget_usd) <= 0:
        return BudgetEscrow(state=EscrowState.disabled,
                            ratio=policy.reserve_ratio,
                            floor_usd=policy.reserve_floor_usd,
                            cap_usd=policy.reserve_cap_usd)
    total = float(budget_usd)
    reserve = total * float(policy.reserve_ratio)
    reserve = max(float(policy.reserve_floor_usd), reserve)
    reserve = min(float(policy.reserve_cap_usd), reserve)
    # 不许把主 agent 挤成零额度：预留最多占一半
    reserve = min(reserve, total * 0.5)
    reserve = round(max(0.0, reserve), 6)
    return BudgetEscrow(
        state=EscrowState.frozen,
        reserve_usd=reserve,
        main_budget_usd=round(total - reserve, 6),
        ratio=policy.reserve_ratio,
        floor_usd=policy.reserve_floor_usd,
        cap_usd=policy.reserve_cap_usd,
        frozen_at=_utcnow(),
    )
