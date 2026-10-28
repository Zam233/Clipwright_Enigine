"""预算与止损（PRD F6 / 开发计划 B5）。

核心语义（与 PRD §12 已定⑥一致）：**预算为可选闸**。

- `pipeline_budget_usd` 留空 → `enabled=False`：只记账（tokens/金额都照记），
  **不阻断、不降级、不返回「预算不足」**；
- 已配置 → 70% 进入 `warn`（模型允许集收窄到 cheap/std），100% 进入 `blocked`
  （拒绝新的派工/重做，落诊断书，管线判 FAILED(budget_exhausted)）。

成本口径：`token 折算货币`——用 `settings.model_prices` 里的每 1k token 单价；
未配置单价的模型按 0 计（宁可少算也不阻断），但 tokens 始终如实统计。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger, settings

WARN_RATIO = 0.7
BLOCK_RATIO = 1.0


def _budget_usd() -> Optional[float]:
    raw = getattr(settings, "pipeline_budget_usd", None)
    try:
        value = float(raw) if raw not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None
    return value if value and value > 0 else None


def budget_enabled() -> bool:
    """是否配置了预算（未配置 = 无上限）。"""
    return _budget_usd() is not None


def _prices() -> dict[str, dict[str, float]]:
    raw = getattr(settings, "model_prices", None) or {}
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            raw = {}
    out: dict[str, dict[str, float]] = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, dict):
                out[str(key)] = {
                    "in": float(val.get("in") or val.get("price_in") or 0.0),
                    "out": float(val.get("out") or val.get("price_out") or 0.0),
                }
    # 路由表里的价格作为兜底来源（避免同一份价格填两处）
    try:
        from clipwright.services.model_router import route_table

        for route in route_table():
            out.setdefault(route.key, {"in": route.price_in, "out": route.price_out})
            out.setdefault(route.model, {"in": route.price_in, "out": route.price_out})
    except Exception:  # noqa: BLE001
        pass
    return out


def _calls(pipeline_id: str) -> list[dict[str, Any]]:
    try:
        from clipwright.services.llm_tracker import get_llm_calls

        return list(get_llm_calls(pipeline_id) or [])
    except Exception as e:  # noqa: BLE001 — 统计失败不得影响管线
        logger.debug("读取 LLM 调用记录失败: %s", e)
        return []


def _cost(call: dict[str, Any], prices: dict[str, dict[str, float]]) -> float:
    return cost_of(
        str(call.get("provider") or ""),
        str(call.get("model") or ""),
        int(call.get("input_tokens") or 0),
        int(call.get("output_tokens") or 0),
        prices,
    )


def cost_of(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    prices: Optional[dict[str, dict[str, float]]] = None,
) -> float:
    """一次调用的美元成本（单价缺失按 0 计——宁可少算也不阻断）。"""
    table = prices if prices is not None else _prices()
    price = table.get(f"{provider}/{model}") or table.get(model) or {"in": 0.0, "out": 0.0}
    tin = max(0, int(input_tokens or 0))
    tout = max(0, int(output_tokens or 0))
    return (tin / 1000.0) * float(price.get("in") or 0.0) + (tout / 1000.0) * float(
        price.get("out") or 0.0
    )


def snapshot(pipeline_id: str) -> dict[str, Any]:
    """预算快照（前端成本条数据源，形状对齐 `BudgetSnapshot`）。"""
    calls = _calls(pipeline_id)
    prices = _prices()
    spent = sum(_cost(c, prices) for c in calls)
    sub_tokens = sum(
        int(c.get("input_tokens") or 0) + int(c.get("output_tokens") or 0)
        for c in calls
        if ":" in str(c.get("agent_name") or "")
    )
    tokens = sum(
        int(c.get("input_tokens") or 0) + int(c.get("output_tokens") or 0) for c in calls
    )
    budget = _budget_usd()
    if budget is None:
        return {
            "pipeline_id": pipeline_id,
            "spent_usd": round(spent, 6),
            "budgetEnabled": False,
            "budget_usd": None,
            "ratio": None,
            "level": "unlimited",
            "tokens": tokens,
            "subagent_tokens": sub_tokens,
            "subagentSpentUsd": round(
                sum(_cost(c, prices) for c in calls
                    if ":" in str(c.get("agent_name") or "")), 6,
            ),
        }
    ratio = spent / budget if budget else 0.0
    level = "blocked" if ratio >= BLOCK_RATIO else ("warn" if ratio >= WARN_RATIO else "ok")
    return {
        "pipeline_id": pipeline_id,
        "spent_usd": round(spent, 6),
        "budgetEnabled": True,
        "budget_usd": budget,
        "ratio": round(min(ratio, 9.99), 4),
        "level": level,
        "tokens": tokens,
        "subagent_tokens": sub_tokens,
        "subagentSpentUsd": round(
            sum(_cost(c, prices) for c in calls
                if ":" in str(c.get("agent_name") or "")), 6,
        ),
    }


def budget_ratio(pipeline_id: str) -> Optional[float]:
    """已用占比；未配置预算时返回 None（调用方据此不做任何收敛）。"""
    return snapshot(pipeline_id).get("ratio")


def is_blocked(pipeline_id: str) -> bool:
    """预算是否已耗尽（未配置预算恒为 False）。"""
    return snapshot(pipeline_id).get("level") == "blocked"
