"""`run_pipeline` 能力（路线 C / C3）：把老 6 阶段管线包成 Kernel 可调用的一步。

为什么留它：C 不等于把管线删掉。常规套路（知识区长片、鬼畜快剪这类有固定做法的选题）
用一条已经调好的流水线做，比让 agent 现编计划更省钱、更稳。**选择权交给 agent**，
但成本对比要可见（PRD F3/F5），所以这里如实把 token/耗时/状态带回去。

三条约束：
1. **双轨开关**：`agent_mode != "on"` 时直接拒（`off` 走老 API，`shadow` 只规划不执行）；
2. **预算前置**：与老入口同一把闸（`services.budget.check_budget`），不因为换了调用方就绕过；
3. **结果可回读**：老 API 的 `/result/{pipeline_id}` 在 agent 模式下要能拿到 timeline，
   所以最近一次管线结果按 task 缓存（兼容层，随 C7 老入口退役一起删）。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger, settings
from clipwright.schema.pipeline import PipelineRequest
from clipwright.schema.task import PlanStep, Task

#: agent 模式下"任务 → 最近一次管线结果"，供老 /result 端点兼容读取
_LAST_RESULT: dict[str, dict[str, Any]] = {}
_MAX_KEEP = 50


def agent_mode() -> str:
    return str(getattr(settings, "agent_mode", "off") or "off").lower()


def enabled() -> bool:
    return agent_mode() == "on"


def last_result(task_id: str) -> Optional[dict[str, Any]]:
    return _LAST_RESULT.get(task_id)


def clear_results() -> None:
    _LAST_RESULT.clear()


def build_request(task: Task, step: PlanStep) -> PipelineRequest:
    """把 Step 参数 + 任务请求合成 PipelineRequest（Step 参数优先）。"""
    args = dict(step.args or {})
    extra = dict(task.request.extra or {})
    extra.update(args.get("extra_params") or {})
    return PipelineRequest(
        persona_id=str(args.get("persona_id") or task.request.persona_id or ""),
        category_plugin_id=str(
            args.get("category_plugin_id") or task.request.category_plugin_id or ""
        ),
        topic=str(args.get("topic") or task.request.topic),
        extra_params=extra,
        dry_run=bool(args.get("dry_run", False)),
    )


async def run(task: Task, step: PlanStep, *, owner: str = "") -> dict[str, Any]:
    """执行一次管线（阻塞直到管线结束）。"""
    if not enabled():
        return {
            "status": "refused",
            "error": (
                f"agent_mode={agent_mode()}：run_pipeline 只在 on 模式下可调用"
                "（off 走老 API，shadow 只规划不执行）"
            ),
        }

    # 预算闸：与老入口同一把（流水线本身也会扣费）
    try:
        from clipwright.services.budget import check_budget

        allowed, used = await check_budget()
        if not allowed:
            return {
                "status": "refused",
                "error": f"本月 token 预算已耗尽（已用 {used}），拒绝再起管线",
            }
    except Exception as e:  # noqa: BLE001 — 预算统计失败不阻断，但要留痕
        logger.warning("预算检查失败（继续执行）: %s", e)

    request = build_request(task, step)
    pipeline_id = str(step.args.get("pipeline_id") or f"pl_{task.id}")

    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

    orch = PipelineOrchestratorV2()
    try:
        state = await orch.run(request, pipeline_id=pipeline_id, task_id=task.id)
    except Exception as e:  # noqa: BLE001 — 管线异常转成 Step 失败，交给 Kernel 收尾
        logger.warning("run_pipeline 能力执行失败 %s: %s", task.id, e)
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    result = state.model_dump(mode="json")
    _remember(task.id, result)
    status = str(getattr(state, "status", "") or "")
    ok = status in ("completed", "pass", "success")
    timeline = (state.shared_data or {}).get("timeline")
    output = {
        "pipeline_id": result.get("pipeline_id", pipeline_id),
        "status": status,
        "tracks": len((timeline or {}).get("tracks") or []),
        "clips": sum(len(t.get("clips") or []) for t in ((timeline or {}).get("tracks") or [])),
        "duration_sec": float((timeline or {}).get("duration_sec") or 0.0),
        "output_path": result.get("output_path") or "",
    }
    return {
        "status": "ok" if ok else "error",
        "output": output,
        "error": None if ok else (result.get("error") or f"管线状态 {status}"),
    }


def _remember(task_id: str, result: dict[str, Any]) -> None:
    _LAST_RESULT[task_id] = result
    if len(_LAST_RESULT) > _MAX_KEEP:
        oldest = next(iter(_LAST_RESULT))
        if oldest != task_id:
            _LAST_RESULT.pop(oldest, None)
