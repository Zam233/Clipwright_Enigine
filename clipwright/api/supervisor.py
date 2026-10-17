"""调度中枢 API（PRD v1.4 / 开发计划 B1 起）。

已交付：
- B1 `GET /api/pipeline/{id}/ledger` —— 账本（可按 stage 过滤）
- B2 `GET /api/pipeline/{id}/attribution` / `GET /{id}/diagnosis`
- B3 `GET /api/pipeline/{id}/decisions`
- B5 `GET /api/pipeline/{id}/budget` / `POST /{id}/dispatch` / `POST /{id}/accept-suggestion`

后续批次在此追加：子代理树、执行日志、审核队列。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipwright.config import logger

router = APIRouter(prefix="/api/pipeline", tags=["supervisor"])


@router.get("/{pipeline_id}/ledger")
async def get_pipeline_ledger(pipeline_id: str, request: Request, stage: str = "") -> dict:
    """返回某条管线的账本（PRD F1）。

    - 内存账本优先（本次进程内执行过的管线）；
    - 内存未命中时回查 Mongo（进程重启后仍可诊断）；
    - 两者都没有：返回 `found=false` 的空结构（不 404，避免前端把「没跑过」
      与「接口不存在」混为一谈）。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.services.episode_ledger import get_ledger

    _enforce_pipeline_owner(request, pipeline_id)

    ledger = get_ledger(pipeline_id, persist=False)
    payload = ledger.to_public()
    if stage:
        payload["entries"] = [e for e in payload["entries"] if e.get("stage") == stage]
        payload["count"] = len(payload["entries"])

    if not payload["found"]:
        mongo_payload = _load_from_mongo(pipeline_id, stage)
        if mongo_payload is not None:
            return mongo_payload

    return payload


def _load_from_mongo(pipeline_id: str, stage: str = "") -> dict | None:
    """内存未命中时回查 Mongo（best-effort，故障时返回 None）。"""
    try:
        from clipwright.context import mongo as mongo_ctx

        if not mongo_ctx.is_connected:
            return None
        doc = mongo_ctx.db["pipeline_ledger"].find_one({"pipeline_id": pipeline_id})
        if not doc:
            return None
        entries = list(doc.get("entries") or [])
        if stage:
            entries = [e for e in entries if isinstance(e, dict) and e.get("stage") == stage]
        attempts: dict[str, int] = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = str(e.get("stage", ""))
            attempts[name] = max(attempts.get(name, 0), int(e.get("attempt", 0) or 0))
        return {
            "pipeline_id": pipeline_id,
            "found": bool(entries),
            "count": len(entries),
            "attempts": attempts,
            "entries": entries,
            "source": "mongo",
        }
    except Exception as e:  # noqa: BLE001 — 诊断接口不得因落库故障 500
        logger.warning("账本 Mongo 回查失败 pipeline=%s: %s", pipeline_id, e)
        return None


# ── B2：归因与诊断书 ─────────────────────────────────────────


def _ledger_entries(pipeline_id: str) -> list:
    """取账本条目（内存优先，Mongo 回退），统一成 LedgerEntry 列表。"""
    from clipwright.services.episode_ledger import get_ledger

    entries = get_ledger(pipeline_id, persist=False).entries()
    if entries:
        return entries

    payload = _load_from_mongo(pipeline_id) or {}
    out = []
    for raw in payload.get("entries") or []:
        try:
            from clipwright.schema.supervisor import LedgerEntry

            out.append(LedgerEntry.model_validate(raw))
        except Exception:  # noqa: BLE001 — 脏条目跳过，不影响其余
            continue
    return out


def _quality_issues(pipeline_id: str) -> list[dict]:
    """从最近一次运行结果里取质检 issues（没有则空）。"""
    try:
        from clipwright.api.pipeline import _pipeline_results

        result = _pipeline_results.get(pipeline_id) or {}
        top = result.get("quality_issues")
        if top:
            return list(top)
        for step in reversed(result.get("steps") or []):
            if not isinstance(step, dict) or step.get("agent_name") != "quality":
                continue
            issues = ((step.get("result") or {}) or {}).get("issues")
            if issues:
                return list(issues)
    except Exception as e:  # noqa: BLE001
        logger.debug("取质检 issues 失败（忽略）: %s", e)
    return []


@router.get("/{pipeline_id}/attribution")
async def get_pipeline_attribution(
    pipeline_id: str, request: Request, llm: bool = False,
) -> dict:
    """归因结论（PRD F2）。

    `llm=false`（默认）只跑确定性规则；`llm=true` 允许规则全不命中时走兜底判定。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.services.attribution import attribute, attribute_with_llm

    _enforce_pipeline_owner(request, pipeline_id)
    entries = _ledger_entries(pipeline_id)
    issues = _quality_issues(pipeline_id)
    attr = (
        await attribute_with_llm(entries, issues=issues) if llm
        else attribute(entries, issues=issues)
    )
    return {"pipeline_id": pipeline_id, "found": bool(entries), **attr.model_dump(mode="json")}


@router.get("/{pipeline_id}/diagnosis")
async def get_pipeline_diagnosis(pipeline_id: str, request: Request) -> dict:
    """诊断书（PRD F5，模板化，无 LLM）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.config import settings
    from clipwright.services.attribution import attribute
    from clipwright.services.diagnosis import build_diagnosis

    _enforce_pipeline_owner(request, pipeline_id)
    entries = _ledger_entries(pipeline_id)
    issues = _quality_issues(pipeline_id)
    attr = attribute(entries, issues=issues)
    report = build_diagnosis(
        entries, attribution=attr, issues=issues,
        mode=getattr(settings, "supervisor_mode", "shadow"), pipeline_id=pipeline_id,
    )
    return {"found": bool(entries), **report.model_dump(mode="json")}


@router.get("/{pipeline_id}/decisions")
async def get_pipeline_decisions(pipeline_id: str, request: Request) -> dict:
    """调度决策序列（PRD F3/F4，调度面板数据源）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner, _pipeline_results

    _enforce_pipeline_owner(request, pipeline_id)
    result = _pipeline_results.get(pipeline_id) or {}
    shared = result.get("shared_data") if isinstance(result, dict) else {}
    shared = shared if isinstance(shared, dict) else {}
    decisions = shared.get("decisions") or []
    return {
        "pipeline_id": pipeline_id,
        "found": bool(decisions),
        "count": len(decisions),
        "accepted_degraded": bool(shared.get("accepted_degraded")),
        "decisions": decisions,
    }


# ── B5：预算与人工接管 ───────────────────────────────────────


@router.get("/{pipeline_id}/budget")
async def get_pipeline_budget(pipeline_id: str, request: Request) -> dict:
    """预算快照（PRD F6）。未配置预算时 level=unlimited、ratio=None。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.services.budget_guard import snapshot

    _enforce_pipeline_owner(request, pipeline_id)
    return snapshot(pipeline_id)


class DispatchOrderBody(BaseModel):
    """人工改派工单（PRD F7）。"""

    target_stage: str = Field(description="要重做的核心阶段")
    reason: str = Field(default="", description="人工改派理由（入账留痕）")
    issues: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/{pipeline_id}/dispatch")
async def post_pipeline_dispatch(
    pipeline_id: str, payload: DispatchOrderBody, request: Request,
) -> dict:
    """人工改派工单（PRD F7）。

    B5 范围：**校验 + 记录 + 留痕**。真正的执行由管线在自愈/重试时消费这些工单
    （返回 `requires_retry=true` 提示调用方走 retry 端点触发）。
    """
    from clipwright.agents.registry import CORE_AGENT_NAMES
    from clipwright.api.pipeline import _enforce_pipeline_owner, _pipeline_results
    from clipwright.services.budget_guard import is_blocked
    from clipwright.services.trace import add_event

    _enforce_pipeline_owner(request, pipeline_id)
    if payload.target_stage not in CORE_AGENT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"目标阶段非法: {payload.target_stage}（只允许核心阶段）",
        )
    if is_blocked(pipeline_id):
        raise HTTPException(status_code=409, detail="预算已耗尽，拒绝新的派工")

    order = {
        "id": f"ord_{uuid4().hex[:8]}",
        "target_stage": payload.target_stage,
        "reason": payload.reason[:300],
        "issues": payload.issues[:5],
        "source": "manual",
    }
    result = _pipeline_results.get(pipeline_id)
    if isinstance(result, dict):
        shared = result.setdefault("shared_data", {})
        if not isinstance(shared, dict):
            shared = {}
            result["shared_data"] = shared
        orders = shared.setdefault("manual_orders", [])
        if isinstance(orders, list):
            orders.append(order)
    add_event(pipeline_id, "supervisor", "dispatch",
              f"人工改派 → {payload.target_stage}：{payload.reason[:80]}", order)
    return {
        "pipeline_id": pipeline_id,
        "order": order,
        "accepted": True,
        "requires_retry": True,
    }


@router.post("/{pipeline_id}/accept-suggestion")
async def post_accept_suggestion(
    pipeline_id: str, request: Request, suggestion_id: str = "",
) -> dict:
    """接受降级 / 一键执行建议（PRD F7）。B5 只处理「接受降级」。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner, _pipeline_results
    from clipwright.services.trace import add_event

    _enforce_pipeline_owner(request, pipeline_id)
    result = _pipeline_results.get(pipeline_id)
    if isinstance(result, dict):
        shared = result.setdefault("shared_data", {})
        if isinstance(shared, dict):
            shared["accepted_degraded"] = True
            decisions = shared.setdefault("decisions", [])
            if isinstance(decisions, list):
                decisions.append({
                    "id": f"dec_{uuid4().hex[:8]}",
                    "strategy": "S4_accept_degraded",
                    "target_stage": "",
                    "reason": f"人工接受降级（suggestion={suggestion_id or '-'}）",
                    "allow_degrade": True,
                    "aborted": False,
                    "stage_attempts": {},
                    "mode": "manual",
                })
    add_event(pipeline_id, "supervisor", "warning", "人工接受降级交付")
    return {"pipeline_id": pipeline_id, "accepted_degraded": True}


