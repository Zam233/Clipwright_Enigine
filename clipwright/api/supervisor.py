"""调度中枢 API（PRD v1.4 / 开发计划 B1 起）。

B1 交付：
- `GET /api/pipeline/{pipeline_id}/ledger` —— 账本（可按 stage 过滤）

后续批次在此追加：归因、诊断书、派工单、子代理树、执行日志、审核队列。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

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

