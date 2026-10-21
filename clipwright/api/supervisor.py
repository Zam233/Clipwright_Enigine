"""调度中枢 API（PRD v1.4 / 开发计划 B1 起）。

已交付：
- B1 `GET /api/pipeline/{id}/ledger` —— 账本（可按 stage 过滤）
- B2 `GET /api/pipeline/{id}/attribution` / `GET /{id}/diagnosis`
- B3 `GET /api/pipeline/{id}/decisions`
- B5 `GET /api/pipeline/{id}/budget` / `POST /{id}/dispatch` / `POST /{id}/accept-suggestion`
- B7/B8 子代理树、取消、派生、worker 审核门
- B9 `POST /{id}/exec`（请求执行 shell/python）+ 执行日志 + 授权/拒绝 + 自动模式
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clipwright.config import logger

router = APIRouter(prefix="/api/pipeline", tags=["supervisor"])
#: 跨管线的管理端点（审核队列等）
admin_router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


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
    auto_retry: bool = Field(
        default=True,
        description="是否立刻按工单重跑该阶段（false=只登记，稍后由调用方触发）",
    )


async def _try_retry(pipeline_id: str, stage: str, request: Request) -> dict[str, Any]:
    """按工单/建议真跑一次重试；失败**不抛**，把原因如实交回前端。

    刻意不复用 `/retry` 端点的 HTTP 语义：调度面板要能显示「已登记但没能重跑，
    因为 xxx」，而不是把 400/404 变成一句「请求失败」。
    """
    if not stage:
        return {"retried": False, "reason": "没有指定目标阶段"}
    try:
        from clipwright.api.pipeline import start_agent_retry

        res = await start_agent_retry(pipeline_id, stage, request)
        return {
            "retried": True,
            "status": str(res.get("status", "")),
            "message": str(res.get("message", "")),
        }
    except HTTPException as e:
        return {"retried": False, "reason": str(e.detail)}
    except Exception as e:  # noqa: BLE001 — 重试失败不得影响工单登记
        logger.warning("调度重试启动失败 %s/%s: %s", pipeline_id, stage, e)
        return {"retried": False, "reason": f"{type(e).__name__}: {e}"}


@router.post("/{pipeline_id}/dispatch")
async def post_pipeline_dispatch(
    pipeline_id: str, payload: DispatchOrderBody, request: Request,
) -> dict:
    """人工改派工单（PRD F7）：校验 → 记录 → 留痕 → **按工单重跑**。

    注意重跑是真的跑（走 `start_agent_retry`，与 `/retry` 端点同一条路径）。
    跑不起来（管线不在内存、目标阶段没有前置成功结果、已在跑）时返回
    `retry.retried=false` + 原因，工单仍然入账——用户可以稍后重试，不会白填理由。
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

    retry_info: dict[str, Any] = {"retried": False, "reason": "未请求自动重跑"}
    if payload.auto_retry:
        retry_info = await _try_retry(pipeline_id, payload.target_stage, request)
    return {
        "pipeline_id": pipeline_id,
        "order": order,
        "accepted": True,
        "requires_retry": not retry_info.get("retried", False),
        "retry": retry_info,
    }


class SuggestionBody(BaseModel):
    """诊断书建议（PRD F7）。`action` 为空时按「接受降级」处理（兼容旧调用）。"""

    suggestion_id: str = Field(default="")
    action: str = Field(default="accept_degraded")
    target_stage: str = Field(default="")


@router.post("/{pipeline_id}/accept-suggestion")
async def post_accept_suggestion(
    pipeline_id: str, request: Request, payload: SuggestionBody | None = None,
    suggestion_id: str = "",
) -> dict:
    """执行诊断书建议：接受降级 / 换模型重跑 / 重做某阶段（PRD F7）。

    - `accept_degraded`（默认）：只登记「已接受降级」，不动产物；
    - `retry_with_model` / `redo_stage`：按 `target_stage` 真跑一次重试；
    - `manual`：不做事，返回 `accepted=false` 并说明需要人工处理。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner, _pipeline_results
    from clipwright.services.trace import add_event

    body = payload or SuggestionBody()
    sid = body.suggestion_id or suggestion_id
    action = (body.action or "accept_degraded").strip()
    _enforce_pipeline_owner(request, pipeline_id)

    if action in ("retry_with_model", "redo_stage"):
        retry_info = await _try_retry(pipeline_id, body.target_stage, request)
        add_event(
            pipeline_id, "supervisor", "warning",
            f"按诊断建议重做 {body.target_stage}（{action}）"
            + ("" if retry_info.get("retried") else f"：未启动（{retry_info.get('reason')}）"),
            {"action": action, "suggestion_id": sid, **retry_info},
        )
        return {
            "pipeline_id": pipeline_id,
            "accepted": bool(retry_info.get("retried")),
            "accepted_degraded": False,
            "action": action,
            "target_stage": body.target_stage,
            "retry": retry_info,
        }

    if action == "manual":
        return {
            "pipeline_id": pipeline_id,
            "accepted": False,
            "accepted_degraded": False,
            "action": action,
            "reason": "该建议需要人工处理，系统不会自动执行",
        }

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
                    "reason": f"人工接受降级（suggestion={sid or '-'}）",
                    "allow_degrade": True,
                    "aborted": False,
                    "stage_attempts": {},
                    "mode": "manual",
                })
    add_event(pipeline_id, "supervisor", "warning", "人工接受降级交付")
    return {
        "pipeline_id": pipeline_id,
        "accepted": True,
        "accepted_degraded": True,
        "action": "accept_degraded",
    }


# ── B7：子代理观测与控制 ─────────────────────────────────────


@router.get("/{pipeline_id}/subagents")
async def get_pipeline_subagents(pipeline_id: str, request: Request) -> dict:
    """子代理树（PRD F12）。无子代理时返回空结构而非 404。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.tracker import get_tracker

    _enforce_pipeline_owner(request, pipeline_id)
    return get_tracker(pipeline_id).snapshot()


@router.post("/{pipeline_id}/subagents/{subagent_id}/cancel")
async def post_cancel_subagent(
    pipeline_id: str, subagent_id: str, request: Request,
) -> dict:
    """取消单个子代理（不可取消/已结束 → 409）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.tracker import get_tracker

    _enforce_pipeline_owner(request, pipeline_id)
    tracker = get_tracker(pipeline_id)
    node = tracker.get(subagent_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"子代理不存在: {subagent_id}")
    if not tracker.cancel(subagent_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "子代理不可取消或已结束"
                f"（status={node.status}, cancellable={node.cancellable}）"
            ),
        )
    return {
        "pipeline_id": pipeline_id,
        "subagent_id": subagent_id,
        "cancelled": True,
        "status": tracker.get(subagent_id).status if tracker.get(subagent_id) else "cancelled",
    }


# ── B8：派生子代理与审核门 ──────────────────────────────────


class SpawnSubagentBody(BaseModel):
    """派生临时 worker（PRD F10）。"""

    role: str = Field(description="角色名，例如「素材可用性核查员」")
    system_prompt: str = Field(default="", description="该角色的任务说明")
    tools: list[str] = Field(default_factory=list, description="工具子集（不得越权）")
    provider: str = Field(default="")
    model: str = Field(default="")
    timeout_sec: float = Field(default=60.0)
    max_turns: int = Field(default=5)


@router.post("/{pipeline_id}/subagents")
async def post_spawn_subagent(
    pipeline_id: str, payload: SpawnSubagentBody, request: Request,
) -> dict:
    """派生一个子代理。

    - 不带执行权限：直接实例化并执行；
    - 带 `run_shell` / `run_python`：返回 `pending_review`，需人工审核后才实例化。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.spawn import SpawnError, spawn_subagent

    _enforce_pipeline_owner(request, pipeline_id)
    ctx = SimpleNamespace(pipeline_id=pipeline_id, extra_params={})
    try:
        return await spawn_subagent(ctx, **payload.model_dump())
    except SpawnError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ReviewWorkerBody(BaseModel):
    approve: bool = Field(description="是否批准实例化")
    confirm_high_risk: bool = Field(default=False, description="risk=high/critical 时必须为 true")


@admin_router.get("/workers/pending-review")
async def get_pending_worker_reviews(request: Request) -> dict:
    """待审核的临时 worker（带独立 LLM 的风险等级与理由）。"""
    from clipwright.supervisor.worker_registry import all_pending_reviews

    items = [w.to_public() for w in all_pending_reviews()]
    return {"count": len(items), "items": items}


@admin_router.post("/workers/{worker_id}/review")
async def post_review_worker(
    worker_id: str, payload: ReviewWorkerBody, request: Request,
) -> dict:
    """人工审核（两级门的第二级）。"""
    from clipwright.supervisor.spawn import SpawnError, review_worker

    try:
        return await review_worker(
            worker_id,
            approve=payload.approve,
            confirm_high_risk=payload.confirm_high_risk,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SpawnError as e:
        raise HTTPException(status_code=400, detail=str(e))


@admin_router.get("/tools")
async def get_supervisor_tools(request: Request) -> dict:
    """主代理可授予的工具全集 + 需要审核的工具（前端审核页说明用）。"""
    from clipwright.supervisor.spawn import worker_tool_matrix

    return worker_tool_matrix()


# ── B9：执行日志与授权 ──────────────────────────────────────


class AutoModeBody(BaseModel):
    enabled: bool = Field(description="自动模式开关（只减免 med，high/critical 仍弹）")


class ApproveExecBody(BaseModel):
    confirm_high_risk: bool = Field(default=False, description="high/critical 必须为 true")
    second_confirm: bool = Field(
        default=False,
        description="critical / 命中硬底线时，再次确认窗口的结果（第二级确认）",
    )


class ExecBody(BaseModel):
    """请求执行一条命令或一段 Python 脚本（PRD F11）。"""

    kind: str = Field(default="shell", description="shell=命令行 / python=脚本")
    command: str = Field(default="", description="kind=shell 时的命令原文")
    code: str = Field(default="", description="kind=python 时的脚本正文")
    cwd: str = Field(default="", description="工作目录；留空=管线 workspace 或临时目录")
    timeout_sec: float = Field(default=0.0, description="0=用设置项 exec_timeout_sec")


@router.post("/{pipeline_id}/exec")
async def post_pipeline_exec(
    pipeline_id: str, payload: ExecBody, request: Request,
) -> dict:
    """请求执行（主代理 / 用户的唯一执行入口）。

    返回三种结局之一，前端据此分流：
    - `done` / `failed`：已执行（低风险，或自动模式下的中风险）；
    - `pending_approval`：`med` 及以上，等用户授权（带危险说明）；
    - `rejected`：命中硬底线或为空，**不可授权**。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.services.budget_guard import is_blocked
    from clipwright.supervisor import gateway as gw

    _enforce_pipeline_owner(request, pipeline_id)
    kind = str(payload.kind or "shell").strip().lower()
    if kind not in ("shell", "python"):
        raise HTTPException(status_code=400, detail=f"不支持的 kind: {kind}")
    if is_blocked(pipeline_id):
        raise HTTPException(status_code=409, detail="预算已耗尽，拒绝新的执行请求")

    timeout_sec = float(payload.timeout_sec or 0.0)  # 0 → 网关回落到设置项 exec_timeout_sec
    if kind == "python":
        out = await gw.run_python(
            pipeline_id, payload.code, cwd=payload.cwd, timeout_sec=timeout_sec,
        )
    else:
        out = await gw.run_command(
            pipeline_id, payload.command, cwd=payload.cwd,
            timeout_sec=timeout_sec, kind="shell",
        )
    return {"pipeline_id": pipeline_id, **out}


@router.get("/{pipeline_id}/exec")
async def get_pipeline_exec(pipeline_id: str, request: Request) -> dict:
    """执行日志（PRD F11）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import records

    _enforce_pipeline_owner(request, pipeline_id)
    items = records(pipeline_id)
    return {"pipeline_id": pipeline_id, "count": len(items), "records": items}


@router.get("/{pipeline_id}/exec/pending-approvals")
async def get_pending_approvals(pipeline_id: str, request: Request) -> dict:
    """待授权的命令（授权弹窗数据源）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import pending_approvals

    _enforce_pipeline_owner(request, pipeline_id)
    items = pending_approvals(pipeline_id)
    return {"pipeline_id": pipeline_id, "count": len(items), "items": items}


@router.post("/{pipeline_id}/exec/{approval_id}/approve")
async def post_approve_exec(
    pipeline_id: str, approval_id: str, payload: ApproveExecBody, request: Request,
) -> dict:
    """授权一条待执行命令（PRD F11）。

    `critical`（含命中硬底线的命令）需要**两级确认**：`confirm_high_risk` + `second_confirm`。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import approve

    _enforce_pipeline_owner(request, pipeline_id)
    try:
        return await approve(
            pipeline_id, approval_id,
            confirm_high_risk=payload.confirm_high_risk,
            second_confirm=payload.second_confirm,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pipeline_id}/exec/{approval_id}/reject")
async def post_reject_exec(pipeline_id: str, approval_id: str, request: Request) -> dict:
    """拒绝一条待执行命令。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import reject

    _enforce_pipeline_owner(request, pipeline_id)
    try:
        return reject(pipeline_id, approval_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{pipeline_id}/exec/auto-mode")
async def get_auto_mode(pipeline_id: str, request: Request) -> dict:
    """读取自动模式（PRD F11：默认关，状态入账）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import auto_mode

    _enforce_pipeline_owner(request, pipeline_id)
    return {"pipeline_id": pipeline_id, "enabled": auto_mode(pipeline_id)}


@router.put("/{pipeline_id}/exec/auto-mode")
async def put_auto_mode(
    pipeline_id: str, payload: AutoModeBody, request: Request,
) -> dict:
    """写入自动模式（用户显式开关；只减免 med 的弹窗）。"""
    from clipwright.api.pipeline import _enforce_pipeline_owner
    from clipwright.supervisor.gateway import set_auto_mode

    _enforce_pipeline_owner(request, pipeline_id)
    enabled = set_auto_mode(pipeline_id, payload.enabled)
    return {"pipeline_id": pipeline_id, "enabled": enabled}


# ── B4 补充：路由表与模式开关（管理员）──────────────────────


class RoutesBody(BaseModel):
    routes: dict[str, list[dict[str, Any]]] | None = None
    whitelist: list[str] | None = None


class ModeBody(BaseModel):
    mode: str = Field(description="off / shadow / on")


def _persist_settings(patch: dict[str, Any]) -> tuple[list[str], list[str], str]:
    """先热更新内存，再尽力持久化（存储不可用不拦请求，但如实回报）。"""
    from clipwright.config import settings as _settings

    for key, value in patch.items():
        try:
            setattr(_settings, key, value)
        except Exception:  # noqa: BLE001
            pass
    try:
        from clipwright.services import settings_store

        applied, cleared = settings_store.save(patch)
        settings_store.apply_all()
        return applied, cleared, "persisted"
    except Exception as e:  # noqa: BLE001 — 落盘失败仍已热生效
        logger.warning("路由/模式配置持久化失败（内存已生效）: %s", e)
        return list(patch), [], "memory_only"


@admin_router.get("/routes")
async def get_model_routes(request: Request) -> dict:
    """模型路由表 + 白名单（PRD F8 / B4）。"""
    from clipwright.services.model_router import (
        _explicit_whitelist,
        allowed_providers,
        route_table,
    )

    stages = ("structure", "material", "edit", "animation", "audio", "quality")
    return {
        "routes": {st: [r.to_public() for r in route_table(st)] for st in stages},
        "whitelist": _explicit_whitelist(),
        "allowed_providers": sorted(allowed_providers()),
    }


@admin_router.put("/routes")
async def put_model_routes(payload: RoutesBody, request: Request) -> dict:
    """写入路由表 / 白名单。`routes={}` 或 `whitelist=[]` 表示回退默认。"""
    patch: dict[str, Any] = {}
    if payload.routes is not None:
        patch["model_routes"] = payload.routes
    if payload.whitelist is not None:
        patch["model_whitelist"] = payload.whitelist
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    applied, cleared, how = _persist_settings(patch)
    return {"applied": applied, "cleared": cleared, "how": how}


@admin_router.get("/mode")
async def get_supervisor_mode(request: Request) -> dict:
    """调度中枢模式（off / shadow / on）。"""
    from clipwright.config import settings as _settings

    return {"mode": getattr(_settings, "supervisor_mode", "off")}


@admin_router.put("/mode")
async def put_supervisor_mode(payload: ModeBody, request: Request) -> dict:
    """切换调度中枢模式（热生效；可持久化）。"""
    if payload.mode not in ("off", "shadow", "on"):
        raise HTTPException(status_code=400, detail=f"非法模式: {payload.mode}")
    applied, cleared, how = _persist_settings({"supervisor_mode": payload.mode})
    return {"mode": payload.mode, "applied": applied, "cleared": cleared, "how": how}


