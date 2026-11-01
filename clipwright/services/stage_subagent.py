"""阶段子代理执行器（PRD §3.1–3.7 / §5）。

`run_stage` 做四件事，顺序固定：

1. **装配输入**（`stage_inputs.assemble`）——缺键立刻回报 `input_insufficient`，不猜；
2. **在限定墙钟与路由下真跑那一个阶段 Agent**（`asyncio.wait_for` + `use_route`）；
3. **结构化回收**（`StageResult`：产物/结论/降级/成本/失败分类/实际模型）；
4. **合并进黑板**（`stage_merge.merge`，`by=stage`，时间线单向覆盖）。

星型拓扑不变：阶段子代理不得再派子代理（深度固定 1 层）。
"""

from __future__ import annotations

from pathlib import Path

import asyncio
import time
from typing import Any, Iterable, Optional

from clipwright.config import logger, settings
from clipwright.schema.agent import AgentDecision
from clipwright.schema.stage import (
    STAGE_NAMES,
    TIMELINE_STAGES,
    StageResult,
    StageSpec,
)
from clipwright.services import stage_inputs, stage_merge, task_blackboard as bb
from clipwright.services.task_events import emit

#: 阶段 → Agent 类（延迟导入：这些 Agent 各自会 import 一堆重量级依赖）
_STAGE_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "structure": ("clipwright.agents.structure_agent", "StructureAgent"),
    "material": ("clipwright.agents.material_agent", "MaterialAgent"),
    "edit": ("clipwright.agents.edit_agent", "EditAgent"),
    "animation": ("clipwright.agents.animation_agent", "AnimationAgent"),
    "audio": ("clipwright.agents.audio_agent", "AudioAgent"),
    "quality": ("clipwright.agents.quality_agent", "QualityAgent"),
}

#: 进程内运行表：task_id → [StageResult]
_RUNS: dict[str, list[StageResult]] = {}
#: 幂等结果缓存：key → StageResult
_IDEM: dict[str, StageResult] = {}
_MAX_IDEM = 256

#: `run_inline`（预览/局部修补）的默认墙钟：比完整阶段短，卡住要尽快报错
DEFAULT_STAGE_TIMEOUT_LOCAL = 1800.0
#: 阶段墙钟下限兜底值：`settings.agent_stage_min_timeout_sec` 读不到时用它
#: （它曾把 animation 设成 900s、material 设成 900s，43 场逐场搜素材必然超时 →
#: 重试 → 再超时，半小时白烧，edit 还因缺 candidate_clips 直接失败）。
MIN_STAGE_TIMEOUT_SEC = 3600.0

#: 阶段工具失败率门槛（E2E 2026-10-30 用户裁定）：样本够、失败率超限 → degraded。
#: 现场：edit 133 次调用失败 60 次（45%）却报 ok，主 agent 看到"工具 133 次（失败 60）"
#: 仍然推进 animation + audio，从来不返工——因为失败率只活在 summary 文案里，
#: 不是**降级事实**。现在进降级语义，Kernel 的降级决策守门会把主 agent 推回来。
TOOL_FAILURE_RATIO_LIMIT = 0.30
#: 判定失败率所需最小样本（少量调用偶发失败不算）
TOOL_FAILURE_MIN_SAMPLES = 8


def stage_timeout_floor() -> float:
    """阶段墙钟下限（秒）。<= 0 表示不设下限（完全信任主 agent 传入值）。"""
    try:
        val = float(getattr(settings, "agent_stage_min_timeout_sec", MIN_STAGE_TIMEOUT_SEC))
    except (TypeError, ValueError):
        val = MIN_STAGE_TIMEOUT_SEC
    return val if val > 0 else 0.0


def effective_stage_timeout(requested: float | None) -> float:
    """把主 agent 自填的 `timeout_sec` 抬到下限之上；<= 0 一律按不设上限（0.0）返回。"""
    try:
        req = float(requested or 0.0)
    except (TypeError, ValueError):
        req = 0.0
    if req <= 0:
        return 0.0
    floor = stage_timeout_floor()
    return max(req, floor) if floor > 0 else req



def agent_for(stage: str) -> Any:
    """实例化阶段 Agent（不复用实例：Agent 内部状态不能跨任务串味）。"""
    dep = _STAGE_DEPENDENCIES.get(str(stage))
    if dep is None:
        raise ValueError(f"阶段 {stage} 没有对应的 Agent（requirements 由需求服务处理）")
    module_name, class_name = dep
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)()


def list_stages(task_id: str) -> list[dict[str, Any]]:
    return [r.to_public() for r in _RUNS.get(str(task_id), [])]


def import_runs(task_id: str, items: list[dict[str, Any]]) -> int:
    """从检查点恢复阶段运行表（S5 状态持久化）；返回恢复条数。

    幂等：已有记录时跳过（避免重复恢复把同一阶段记两遍）。
    """
    tid = str(task_id)
    if _RUNS.get(tid):
        return 0
    restored: list[StageResult] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            restored.append(StageResult.model_validate(item))
        except Exception:  # noqa: BLE001 — 单条坏数据跳过
            continue
    if restored:
        _RUNS[tid] = restored
        logger.info("已恢复阶段运行表 %s（%d 条）", tid, len(restored))
    return len(restored)


def get_stage(task_id: str, stage: str) -> Optional[dict[str, Any]]:
    for result in reversed(_RUNS.get(str(task_id), [])):
        if result.stage == str(stage):
            return result.to_public()
    return None


def reset(task_id: str = "") -> None:
    if task_id:
        _RUNS.pop(str(task_id), None)
    else:
        _RUNS.clear()
        _IDEM.clear()


def _record(task_id: str, result: StageResult) -> None:
    _RUNS.setdefault(str(task_id), []).append(result)


def _default_route() -> tuple[str, str]:
    """留空继承默认模型时，也要把**实际生效**的 provider/model 回填给主 Agent。"""
    from clipwright.services.llm import _resolve_role_binding  # noqa: PLC2701 — 同一份绑定逻辑

    bound = _resolve_role_binding("main") or {}
    provider = str(bound.get("provider") or getattr(settings, "llm_provider", "") or "")
    model = str(bound.get("model") or getattr(settings, "llm_model", "") or "")
    return provider, model


def _cost_from_usage(before: dict[str, int], after: dict[str, int],
                     route: Any = None) -> tuple[Optional[float], bool]:
    """按进程级用量差值估算成本；无用量/无价目表 → None（不许伪造 0）。"""
    if not after or not before:
        return None, True
    delta_in = int(after.get("input_tokens", 0)) - int(before.get("input_tokens", 0))
    delta_out = int(after.get("output_tokens", 0)) - int(before.get("output_tokens", 0))
    if delta_in <= 0 and delta_out <= 0:
        return None, True
    try:
        from clipwright.services.budget_guard import cost_of

        # 显式 route 时按 route 的 provider/model 计价（否则会按默认模型价目算错）
        default_provider, default_model = _default_route()
        provider = str(getattr(route, "provider", "") or "") or default_provider
        model = str(getattr(route, "model", "") or "") or default_model
        return round(float(cost_of(provider, model, delta_in, delta_out)), 6), False
    except Exception:  # noqa: BLE001 — 记账失败不影响阶段结果
        return None, True


def tool_failure_degradation(
    stage: str, tool_calls: list[dict[str, Any]] | None,
) -> tuple[bool, dict[str, Any] | None]:
    """工具失败率是否构成**降级事实**（E2E 2026-10-30 用户裁定）。

    现场：edit 阶段 133 次工具调用失败 60 次（45%），阶段仍报 `ok`，主 agent
    看到 summary 里的「工具 133 次（失败 60）」照样推进 animation + audio，
    从来不返工——因为失败率只活在文案里，不在降级语义里。

    口径：样本 ≥ `TOOL_FAILURE_MIN_SAMPLES` 且失败率 ≥ `TOOL_FAILURE_RATIO_LIMIT`
    → 返回 (True, 降级条目)。降级条目进 StageResult → AgentResult → 工具结果，
    Kernel 的降级决策守门会把主 agent 推回来，要求重派该阶段。
    """
    calls = [c for c in (tool_calls or []) if isinstance(c, dict)]
    total = len(calls)
    if total < TOOL_FAILURE_MIN_SAMPLES:
        return False, None
    failed = [
        c for c in calls
        if str(c.get("status") or "").lower() not in ("success", "ok", "")
    ]
    ratio = len(failed) / total
    if ratio < TOOL_FAILURE_RATIO_LIMIT:
        return False, None
    by_tool: dict[str, int] = {}
    for c in failed:
        key = str(c.get("tool") or c.get("capability") or "?")
        by_tool[key] = by_tool.get(key, 0) + 1
    top = sorted(by_tool.items(), key=lambda kv: -kv[1])[:3]
    return True, {
        "kind": "stage_tool_failures",
        "detail": (f"{stage} 阶段工具失败 {len(failed)}/{total}"
                   f"（{ratio:.0%}，阈值 {TOOL_FAILURE_RATIO_LIMIT:.0%}）；"
                   f"失败最多：{', '.join(f'{k}×{v}' for k, v in top)}"),
        "scope": stage,
        "impact": "本阶段产物可能缺件（该切的没切/该裁的没裁），下游会把它当成功继续用",
    }


def _summary_of(stage: str, payload: dict[str, Any],
                tool_calls: list[dict[str, Any]] | None = None) -> str:
    """给主 Agent 的一句话结论（关键数值与**异常信号**都不许丢）。

    现代 agent 化（用户 2026-10-29）：主 agent 不该只看到"轨道 6 条 / 片段 502 个"
    这种 happy-path 数字。工具调用次数、失败次数、降级条数、耗时同样是一句话的
    组成部分——它们决定主 agent 下一步是继续、重派还是换策略。
    """
    bits: list[str] = []
    if stage == "structure":
        scenes = payload.get("scenes") or []
        bits.append(f"分镜 {len(scenes)} 个")
        skeleton = payload.get("script_skeleton") or {}
        if isinstance(skeleton, dict) and skeleton.get("title"):
            bits.append(f"标题「{str(skeleton['title'])[:40]}」")
    if stage == "material":
        bits.append(f"候选素材 {len(payload.get('candidate_clips') or [])} 条")
    if stage in TIMELINE_STAGES:
        tl = payload.get("timeline") or {}
        tracks = tl.get("tracks") if isinstance(tl, dict) else None
        if isinstance(tracks, list):
            clips = sum(len((t or {}).get("clips") or []) for t in tracks if isinstance(t, dict))
            bits.append(f"轨道 {len(tracks)} 条 / 片段 {clips} 个")
        if isinstance(tl, dict) and tl.get("duration") is not None:
            bits.append(f"时长 {float(tl.get('duration') or 0):.1f}s")
        if stage == "animation" and payload.get("animation_plan"):
            bits.append("动画已编排")
    if stage == "quality":
        issues = payload.get("issues") or []
        errors = [i for i in issues if str((i or {}).get("severity")) == "error"]
        bits.append(f"问题 {len(issues)} 条（error {len(errors)}）")
        if payload.get("redo_agent"):
            bits.append(f"建议重做 {payload.get('redo_agent')}")
    # ── 异常信号（必须进一句话，别让主 agent 只看到 happy path）──
    degradations = payload.get("degradations") or []
    if degradations:
        kinds = sorted({str(getattr(d, "kind", None) or (d or {}).get("kind") or "")
                        for d in degradations} - {""})
        bits.append(f"降级 {len(degradations)} 项" + (f"（{'/'.join(kinds[:3])}）" if kinds else ""))
    tool_calls = tool_calls if tool_calls is not None else (
        payload.get("tool_calls") or payload.get("_tool_calls") or [])
    if isinstance(tool_calls, list) and tool_calls:
        failed = [c for c in tool_calls
                  if str((c or {}).get("status") or "").lower() not in ("success", "ok", "")]
        bits.append(f"工具 {len(tool_calls)} 次" + (f"（失败 {len(failed)}）" if failed else ""))
    if not bits:
        bits.append(f"{stage} 完成")
    return "；".join(bits)[:500]


def _failure_from_output(payload: dict[str, Any], *, stage: str) -> Any:
    raw = payload.get("failure")
    if isinstance(raw, dict) and raw:
        from clipwright.schema.agent import FailureReport

        try:
            return FailureReport(**raw)
        except Exception:  # noqa: BLE001
            pass
    if str(payload.get("decision") or "") in (AgentDecision.FAIL.value, "fail") or payload.get("error"):
        from clipwright.schema.agent import FailureReport

        return FailureReport(
            category="unknown",
            blocking_point=str(payload.get("error") or f"{stage} 阶段失败")[:200],
        )
    return None


async def run_inline(stage: str, input_obj: Any, context: Any, *,
                     timeout_sec: float | None = None) -> Any:
    """直接跑一个阶段 Agent（**不经过黑板**）。

    用途：需求侧的"预览/局部修补"——例如给单个片段换素材、生成分镜预览。
    这类操作不是完整阶段（没有 Task、不该污染黑板），但它们**必须复用同一个
    Agent 实例化与墙钟通道**，否则会出现"管线里能跑、预览里跑不通"的两套行为。

    返回 Agent 的输出对象（`XOutput`）；异常原样抛出，由调用方降级。
    """
    agent = agent_for(stage)
    timeout = float(timeout_sec or DEFAULT_STAGE_TIMEOUT_LOCAL)
    from clipwright.services.degradation import degradation_scope

    degradations: list[Any] = []
    with degradation_scope(degradations):
        output = await asyncio.wait_for(agent.execute(input_obj, context), timeout=timeout)
    try:
        if degradations and hasattr(output, "degradations"):
            existing = list(getattr(output, "degradations") or [])
            seen = {(d.kind, d.detail) for d in existing}
            for d in degradations:
                if (d.kind, d.detail) not in seen:
                    existing.append(d)
                    seen.add((d.kind, d.detail))
            output.degradations = existing
    except Exception:  # noqa: BLE001 — 降级并入失败不影响结果
        pass
    return output


async def run(task: Any, spec: StageSpec) -> dict[str, Any]:
    """能力层入口：返回 Kernel 可直接消费的 dict（status/output/failure/…）。"""
    task_id = str(getattr(task, "id", "") or "")
    if not task_id:
        return {"status": "refused", "error": "缺少 task_id"}
    if str(spec.stage) not in STAGE_NAMES:
        return {"status": "refused", "error": f"未知阶段: {spec.stage}"}

    idem = (spec.idem_key or "").strip()
    if idem and idem in _IDEM:
        cached = _IDEM[idem].model_copy(deep=True)
        return {
            "status": "ok" if cached.status in ("ok", "degraded") else "error",
            "output": cached.to_public() | {"replayed": True},
            "cost_usd": cached.cost_usd,
        }

    result = await run_stage(task, spec)
    if idem:
        if len(_IDEM) >= _MAX_IDEM:
            _IDEM.pop(next(iter(_IDEM)))
        _IDEM[idem] = result.model_copy(deep=True)

    if result.status in ("ok", "degraded", "dry_run"):
        return {
            "status": "ok",
            "output": result.to_public(),
            "cost_usd": result.cost_usd,
            "cost_unknown": result.cost_unknown,
        }
    if result.status == "cancelled":
        return {
            "status": "error",
            "error": result.error or "阶段被取消",
            "output": result.to_public(),
            "failure": {
                "category": "cancelled",
                "retryable": "permanent",
                "blocking_point": result.error or "人工取消",
                "evidence": [],
            },
        }
    return {
        "status": "error" if result.status == "failed" else "refused",
        "error": result.error or f"阶段 {spec.stage} 状态 {result.status}",
        "output": result.to_public(),
        "failure": (result.failure.model_dump(mode="json") if result.failure else {
            "category": "unknown", "retryable": "unknown",
            "blocking_point": result.error or result.status, "evidence": [],
        }),
    }


def _stage_progress_counts(task_id: str) -> tuple[int, int]:
    """(已完成阶段数, 计划阶段数)。拿不到就给 (n, 0) —— 让播报写"总数未定"。"""
    try:
        from clipwright.services.workspace import Workspace
        from clipwright.services import stage_report

        root = Workspace(str(task_id)).dirs().root
        done = len({m["stage"] for m in stage_report.list_reports(root, limit=200)})
        return done, len(PLAN_STAGES)
    except Exception:  # noqa: BLE001 — 统计失败按"总数未定"处理
        return 0, 0


#: 计划里的六个阶段（进度分母；主 agent 临时加派不改变分母，播报里会写"计划已调整"）
PLAN_STAGES: tuple[str, ...] = ("structure", "material", "edit", "animation",
                                "audio", "quality")


async def run_stage(task: Any, spec: StageSpec) -> StageResult:
    """真跑一个阶段，返回结构化结果（并合并进黑板）。"""
    task_id = str(getattr(task, "id", "") or "")
    stage = str(spec.stage)
    # 显式 inputs 时**不读黑板来"补齐必需键"**（PRD §16.2 / S2：数据必须过主 agent 的手），
    # 但空值回退需要黑板（见 stage_inputs.assemble 的 _flow_keys）——二者不冲突：
    # 校验只认显式键，取值时空值才回退。
    explicit = dict(spec.inputs or {})
    board = bb.get(task_id)
    started = time.monotonic()

    emit(task_id, "step_start", f"阶段子代理：{stage}",
         {"stage": stage, "args_keys": sorted((spec.args or {}).keys()),
          "inputs_keys": sorted(explicit.keys()),
          "inputs_source": "explicit" if explicit else "blackboard",
          "blackboard_keys": sorted(board.keys())})

    # 进度播报（PRD §14）：阶段开始。`completed/total` 取「已落报告数 / 六阶段」，
    # 拿不到就写"总数未定"，绝不编百分比。
    try:
        from clipwright.services import progress as _progress

        _done, _total = _stage_progress_counts(task_id)
        _progress.emit_progress(
            task_id, trigger="stage_start", stage=stage,
            completed=_done, total=_total,
            action=f"派发 {stage}",
            next_action="等该阶段返回，再看是否需要返工",
            once_key=f"stage_start:{stage}")
    except Exception as exc:  # noqa: BLE001 — 播报失败不影响跑阶段
        logger.warning("进度播报（阶段开始）失败 %s/%s: %s", task_id, stage, exc)

    # 人工「追问/注入」（PRD modern-agent-review M2）：往这个阶段的子会话里塞的要求，
    # 下一次派发时并入 brief 并逐条上报——不然"我让某个子 agent 改"这件事只有我知道。
    try:
        from clipwright.services import agent_run as _agent_run

        _notes = _agent_run.take_pending_notes(task_id, stage)
    except Exception:  # noqa: BLE001 — 便条不可用不影响跑阶段
        _notes = []
    if _notes:
        _joined = "\n".join(f"- {n}" for n in _notes)
        spec = spec.model_copy(update={
            "brief": (str(spec.brief or "").strip() + "\n\n[人工补充要求]\n" + _joined).strip()
        }) if hasattr(spec, "model_copy") else spec
        emit(task_id, "inject", f"{stage} 收到 {len(_notes)} 条人工补充要求",
             {"stage": stage, "notes": _notes[:5]}, span="subagent")

    # 1) 装配输入
    try:
        if explicit:
            # board 一起传：黑板已有的键也算满足（否则模型会把大对象内联进
            # tool_call 参数，参数被截断就整条任务 failed）
            _needs = stage_inputs.validate(stage, explicit, board)
            if _needs:
                raise stage_inputs.StageInputMissing(
                    stage, [str(n["field"]) for n in _needs],
                    [str(n.get("from_stage") or "structure") for n in _needs],
                )
        input_obj = stage_inputs.assemble(
            stage, task=task, board=board,
            override={**explicit, **(spec.args or {})},
        ) if stage != "requirements" else {
            "topic": getattr(getattr(task, "request", task), "topic", ""),
            **(spec.args or {}),
        }
    except stage_inputs.StageInputMissing as exc:
        result = StageResult(
            stage=stage,
            status="failed",
            error=str(exc),
            summary=f"缺输入：{'、'.join(exc.missing)}",
            failure=_missing_failure(stage, exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        emit(task_id, "step_end", f"阶段 {stage} 无法开始：{result.error}",
             {"stage": stage, "status": result.status,
              "missing": exc.missing, "suggestion": exc.suggestion})
        return result
    except Exception as exc:  # noqa: BLE001 — 装配异常同样要如实回报
        result = StageResult(
            stage=stage, status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        return result

    if spec.dry_run:
        result = StageResult(
            stage=stage, status="dry_run",
            summary=f"输入已装配（{type(input_obj).__name__}），未执行",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        return result

    # 2) 路由：显式 provider/model 只校验「已配置 provider + 模型可用」，
    #    不受 model_whitelist 阻断（2026-10-28 PRD B3）；解析失败如实拒绝，
    #    绝不静默回退默认模型（用户以为派了 X、实际跑默认模型属于欺骗）。
    route = None
    if spec.provider or spec.model:
        from clipwright.services.model_router import resolve_explicit_route

        resolved = resolve_explicit_route(spec.provider, spec.model)
        if not resolved.ok:
            result = StageResult(
                stage=stage, status="refused",
                error=f"显式模型不可用：{resolved.message}",
                failure=_policy_failure(
                    f"{spec.provider or '<默认>'}/{spec.model or '<默认>'}",
                    category=resolved.reason or "provider_not_configured",
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            _record(task_id, result)
            return result
        route = resolved.route

    # 3) 执行
    from clipwright.services.degradation import degradation_scope
    from clipwright.services.llm import llm_usage_snapshot
    from clipwright.services.model_router import use_route

    context = stage_inputs.build_context(task)
    usage_before = llm_usage_snapshot()
    degradations: list[Any] = []
    payload: dict[str, Any] = {}
    # 阶段墙钟下限（E2E 2026-10-29）：`timeout_sec` 是主 agent 可以自己填的参数，
    # 实测它填了 900s → material（43 场逐场搜素材 + 视觉 LLM）与 animation（43 段 MG
    # 逐个渲染）必然超时，然后自己重试、再超时，白烧半小时。模型不该有权把阶段超时
    # 设到"注定失败"的量级；<= 0 视为不设上限（见 clipwright/services/timeouts.py）。
    timeout_eff = effective_stage_timeout(spec.timeout_sec)
    try:
        agent = agent_for(stage)
        # 让 `subagent_tool` 事件带上 run_id（PRD §13.3）：与 `agent_run` 用同一个幂等键，
        # 前端才能把"这次工具调用"归到具体哪一次派发上（全部可见时靠它分组）。
        try:
            # run_id 优先：重放/降级重跑有独立执行 id，不能再把工具事件挂回旧 run。
            agent._current_run_id = str(  # noqa: SLF001
                getattr(spec, "run_id", "") or getattr(spec, "idem_key", "") or ""
            )
        except Exception:  # noqa: BLE001 — 设置失败只影响事件里的一个字段
            pass
        # 子 agent 运行上下文（现代 agent 化）：阶段内部的每次 call_tool 都会归到本任务
        # 并发 `subagent_tool` 事件——长阶段（material 15 分钟 / animation 逐段渲染）
        # 在事件流里看得见进度，而不是等它跑完只给一句 summary。
        from clipwright.services.agent_context import task_scope

        with task_scope(task_id, stage=stage), degradation_scope(degradations), use_route(route):
            from clipwright.services.timeouts import wait_or_run

            output = await wait_or_run(agent.execute(input_obj, context), timeout_eff)
        payload = stage_merge.payload_of(output)
        if degradations and hasattr(output, "degradations"):
            try:
                output.degradations = list(getattr(output, "degradations") or []) + [
                    d for d in degradations
                    if (d.kind, d.detail) not in {
                        (x.kind, x.detail) for x in (getattr(output, "degradations") or [])
                    }
                ]
                payload = stage_merge.payload_of(output)
            except Exception:  # noqa: BLE001
                pass
    except asyncio.TimeoutError:
        result = StageResult(
            stage=stage, status="failed",
            error=f"阶段超时（>{timeout_eff}s）",
            failure=_timeout_failure(stage, timeout_eff),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        emit(task_id, "step_end", f"阶段 {stage} 超时", {"stage": stage, "status": "failed"})
        return result
    except asyncio.CancelledError:
        result = StageResult(
            stage=stage, status="cancelled", error="阶段被取消",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        raise
    except Exception as exc:  # noqa: BLE001 — 阶段失败不能带崩 Kernel
        logger.warning("阶段 %s 执行失败 %s: %s", stage, task_id, exc)
        result = StageResult(
            stage=stage, status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
            failure=_internal_failure(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(task_id, result)
        emit(task_id, "step_end", f"阶段 {stage} 失败：{exc}",
             {"stage": stage, "status": "failed"})
        return result

    usage_after = llm_usage_snapshot()
    cost, cost_unknown = _cost_from_usage(usage_before, usage_after, route)
    # 归因（modern-agent PRD §2.4.2）：token 与工具轨迹随 StageResult 上交，
    # 上层（AgentResult / 评估 / 前端成本条）才有真实数字可用。
    # token 取进程级用量差值 —— 与 cost 同一口径（都是"这个阶段实际花掉的"）。
    tokens = max(0, (int(usage_after.get("input_tokens", 0))
                     + int(usage_after.get("output_tokens", 0)))
                 - (int(usage_before.get("input_tokens", 0))
                    + int(usage_before.get("output_tokens", 0))))
    stage_tool_calls = list(getattr(agent, "last_tool_calls", None) or [])
    # 暗区补齐（PRD M2）：素材源检索 / MG 生成 / 插件与 skill 调用不走 call_tool，
    # 但同样属于"子 agent 干了什么"。`operation()` 记下的条目在这里并入轨迹
    # （source=internal），随 tool_calls 一起进 AgentResult 与落盘文件。
    try:
        from clipwright.services import agent_context as _agent_ctx

        _internal_ops = _agent_ctx.take_operations()
        if _internal_ops:
            stage_tool_calls = stage_tool_calls + _internal_ops
    except Exception:  # noqa: BLE001 — 操作表不可用不影响阶段
        pass

    # 4) 合并
    writes: list[str] = []
    merge_errors: list[str] = []
    if spec.merge:
        try:
            writes, merge_errors = stage_merge.merge(stage, payload, task_id=task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("阶段合并失败 %s.%s: %s", task_id, stage, exc)
            merge_errors = [f"merge: {exc}"]

    failure = _failure_from_output(payload, stage=stage)
    # 工具失败率门禁（E2E 2026-10-30 用户裁定）：见 tool_failure_degradation()。
    _tool_gate_hit, _tool_failure_deg = tool_failure_degradation(stage, stage_tool_calls)

    status = "ok"
    if failure is not None:
        status = "failed"
    elif merge_errors:
        # 合并失败（例如时间线没能落黑板）必须降级为 degraded：
        # 旧实现只打 warning，阶段报 ok 但下游拿不到 timeline，故障在下游才炸。
        status = "degraded"
    elif _tool_gate_hit:
        status = "degraded"
    elif payload.get("degradations") or (payload.get("decision") == "pass" and writes == []
                                        and stage in TIMELINE_STAGES):
        status = "degraded"

    _degradations = [d if isinstance(d, dict) else
                     getattr(d, "model_dump", lambda **_: {})()
                     for d in (payload.get("degradations") or [])][:20]
    if _tool_failure_deg is not None:
        _degradations.append(_tool_failure_deg)
    _degradations += [{"kind": "blackboard_merge_failed", "detail": e,
                       "scope": stage, "impact": "下游阶段可能拿不到该产物"}
                      for e in merge_errors[:5]]

    # 进度播报（PRD §14）：阶段结束（写清状态/降级/下一步）
    try:
        from clipwright.services import progress as _progress

        _done2, _total2 = _stage_progress_counts(task_id)
        _progress.emit_progress(
            task_id, trigger="stage_end", stage=stage,
            completed=_done2, total=_total2,
            action=f"{stage} 返回 {status}",
            conclusion=("有降级：" + ",".join(sorted({str(d.get('kind')) for d in _degradations})[:4])
                         if _degradations else "没有降级"),
            next_action="主 agent 判断是否返工" if status != "ok" else "继续下一阶段",
            once_key=f"stage_end:{stage}:{attempt_no}")
    except Exception as exc:  # noqa: BLE001 — 播报失败不影响阶段结果
        logger.warning("进度播报（阶段结束）失败 %s/%s: %s", task_id, stage, exc)

    result = StageResult(
        stage=stage,
        status=status,
        artifacts={k: payload[k] for k in ("timeline", "script_skeleton",
                                           "candidate_clips", "issues") if payload.get(k)},
        summary=_summary_of(stage, payload, tool_calls=stage_tool_calls),
        writes=writes,
        degradations=_degradations,
        issues=list(payload.get("issues") or [])[:20],
        redo_agent=str(payload.get("redo_agent") or ""),
        cost_usd=cost,
        cost_unknown=cost_unknown,
        tokens=tokens,
        tool_calls=stage_tool_calls,
        duration_ms=int((time.monotonic() - started) * 1000),
        provider=str(getattr(route, "provider", "") or "") or _default_route()[0],
        model=str(getattr(route, "model", "") or "") or _default_route()[1],
        failure=failure,
        error=str(payload.get("error") or ""),
    )

    # ── 报告落盘（PRD §17.3 / §18.3）：子 agent 的文字汇报由此写进工作区 ──
    root = _workspace_root_of(task)
    if root:
        from clipwright.services import stage_report

        # attempt = 该阶段在本任务里跑过几次（含本次），用于报告文件名 03-edit-2.md
        attempt_no = sum(1 for r in _RUNS.get(task_id, []) if r.stage == stage) + 1
        # 产物先落盘再写报告：引用里只带 preview，大对象留给 read_artifact 按需读
        try:
            result.artifacts = persist_artifacts(root, attempt_no, result.artifacts, stage=stage)
        except Exception as exc:  # noqa: BLE001 — 落盘失败保持内联，不阻断
            logger.warning("产物落盘失败 %s/%s: %s", task_id, stage, exc)
        sections = stage_report.build_sections(
            payload, stage=stage, status=status, summary=result.summary,
            needs=[n for n in (payload.get("needs") or []) if isinstance(n, dict)],
            degradations=_degradations, issues=result.issues,
            artifact_names=list((payload.get("artifacts") or {}).keys()) if isinstance(
                payload.get("artifacts"), dict) else list(result.artifacts.keys()),
        )
        path, seq = "", 0
        try:
            path, seq = stage_report.write_report(
                workspace_root=root, stage=stage, status=status, attempt=attempt_no,
                sections=sections, artifacts=_artifact_refs(result), cost_usd=cost,
                duration_ms=result.duration_ms, provider=result.provider, model=result.model,
                task_id=task_id,
            )
        except Exception as exc:  # noqa: BLE001 — 写报告失败降级，不阻断阶段
            logger.warning("写报告异常 %s/%s: %s", root, stage, exc)
        result.report_path, result.report_seq = path, seq
        # 工具轨迹全量落盘（PRD modern-agent-review M1）：内存里 `last_tool_calls` 只留
        # 最近 200 条（性能），完整 transcript 落 `reports/tool_calls/<stage>-<n>.json`，
        # 主 agent 按 `tool_calls_path` 回查——"可见"不能只是"最近 20 条采样"。
        try:
            result.tool_calls_path = _dump_tool_calls(
                root, stage=stage, attempt=attempt_no, calls=stage_tool_calls,
            )
        except Exception as exc:  # noqa: BLE001 — 落盘失败不阻断阶段
            logger.warning("工具轨迹落盘失败 %s/%s: %s", task_id, stage, exc)
        if not path:
            # 报告没写成 = 主 agent 读不到文字汇报 → 降级 + 事件（M29：不能只改 status）
            result.status = "degraded"
            result.degradations.append({
                "kind": "report_write_failed", "detail": "报告落盘失败（reports/ 不可写？）",
                "scope": stage, "impact": "主 agent 读不到本阶段文字汇报",
            })
            emit(task_id, "degradation", f"报告落盘失败：{stage}",
                 {"kind": "report_write_failed", "stage": stage,
                  "reason": "reports_dir_unwritable"})
        else:
            emit(task_id, "doc_write", f"报告已写：{path}",
                 {"path": path, "bytes": 0, "by": stage, "seq": seq})

    _record(task_id, result)
    emit(task_id, "step_end", f"阶段 {stage} → {result.status}：{result.summary}"[:200],
         {"stage": stage, "status": result.status, "writes": writes,
          "cost_usd": cost, "duration_ms": result.duration_ms,
          "summary": result.summary, "report_path": result.report_path,
          "report_seq": result.report_seq})
    return result


def _dump_tool_calls(root: str, *, stage: str, attempt: int,
                     calls: list[dict[str, Any]]) -> str:
    """把阶段全量工具轨迹写成 JSON（`reports/tool_calls/<stage>-<attempt>.json`）。

    为什么单独落盘：`BaseAgent.last_tool_calls` 与 `AgentResult.tool_payload` 都有条数上限
    （200 / 12 条采样），而 edit 这类阶段可能有 100+ 次调用。主 agent 要复盘子 agent
    到底干了什么（PRD modern-agent-review P0），就必须有**无损**的那一份。
    """
    if not root or not calls:
        return ""
    import json
    from pathlib import Path

    target_dir = Path(root) / "reports" / "tool_calls"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{stage}-{max(1, int(attempt))}.json"
    payload = {
        "stage": stage,
        "attempt": int(attempt),
        "count": len(calls),
        "calls": calls,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def _workspace_root_of(task: Any) -> str:
    """取任务的工作区根：`request.extra.workspace_root` 优先，其次按 task_id 推断。"""
    try:
        request = getattr(task, "request", task)
        extra = dict(getattr(request, "extra", {}) or {})
        root = str(extra.get("workspace_root") or "")
        if root:
            return root
        from clipwright.services.workspace import Workspace

        ws = Workspace(str(getattr(task, "id", "") or ""))
        return str(ws.dirs().root) if ws.exists else ""
    except Exception:  # noqa: BLE001 — 拿不到根就跳过报告（不阻断阶段）
        return ""


def _artifact_refs(result: StageResult) -> dict[str, Any]:
    """把产物整理成报告里的引用表（大对象只留指针 + preview）。"""
    out: dict[str, Any] = {}
    for name, value in (result.artifacts or {}).items():
        if isinstance(value, dict) and value.get("artifact"):
            out[name] = value
            continue
        preview = value
        if isinstance(value, dict):
            preview = {k: value[k] for k in list(value)[:4]}
        elif isinstance(value, list):
            preview = f"{len(value)} 项"
        out[name] = {"artifact": "", "preview": str(preview)[:200]}
    return out


def persist_artifacts(root: str | Path, seq: int, artifacts: dict[str, Any],
                      *, stage: str = "") -> dict[str, Any]:
    """把阶段产物写成不可变文件，并把 `StageResult.artifacts` 换成引用（PRD §17.5）。

    为什么必须落盘：产物内联在结果里，`get_stage` / 事件 / 报告都会把它整包带出来；
    实测量级下一条时间线可达数 MB。落盘后主 agent 用 `read_artifact` 按需读取，
    引用里只留 `{artifact, path, sha256, schema, bytes, preview}`。
    """
    import hashlib
    import json
    from pathlib import Path as _P

    try:
        base = _P(root) / "artifacts"
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return artifacts

    refs: dict[str, Any] = {}
    for name, value in (artifacts or {}).items():
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001 — 序列化失败就保持内联
            refs[name] = value
            continue
        size = len(text.encode("utf-8"))
        preview = value
        if isinstance(value, dict):
            preview = {k: value[k] for k in list(value)[:4]}
        elif isinstance(value, list):
            preview = f"{len(value)} 项"
        if size < 512:
            # 小对象不值得落盘（引用比内容还长）
            refs[name] = value
            continue
        artifact_id = f"art_{hashlib.sha256(f'{seq}:{stage}:{name}'.encode()).hexdigest()[:8]}"
        path = base / f"{artifact_id}.json"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("产物落盘失败 %s/%s: %s", stage, name, exc)
            refs[name] = value
            continue
        refs[name] = {
            "artifact": artifact_id,
            "path": str(path),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "schema": type(value).__name__,
            "bytes": size,
            "preview": preview if not isinstance(preview, dict) else
                       {k: str(v)[:60] for k, v in preview.items()},
        }
    return refs


# ── 失败分类（主 Agent 据此决策，PRD §5.5）──────────────────


def _missing_failure(stage: str, exc: stage_inputs.StageInputMissing) -> Any:
    from clipwright.schema.agent import FailureReport

    return FailureReport(
        category="input_insufficient",
        blocking_point=str(exc)[:200],
        retryable="needs_upstream",
        # needs 是契约里的"请求上游补什么"，主 Agent 据此先派缺的那一步
        needs=[{"from_stage": s, "field": "、".join(exc.missing), "why": f"{stage} 阶段的必需输入"}
               for s in (exc.suggestion or ["structure"])],
        evidence=[{"kind": "missing_keys", "text": "、".join(exc.missing)}],
    )


def _timeout_failure(stage: str, timeout_sec: float) -> Any:
    from clipwright.schema.agent import FailureReport

    return FailureReport(
        category="timeout",
        blocking_point=f"{stage} 超过 {timeout_sec}s",
        retryable="retry_same",
    )


def _policy_failure(key: str, category: str = "policy_refused") -> Any:
    """策略拒绝的失败报告。

    `category` 默认仍是 `policy_refused`（白名单时代语义）；显式 route 解析失败时
    带上具体原因（`provider_not_configured` / `model_not_configured` …），
    让主 agent 能看出「是 provider 没配」还是「模型名不对」（PRD B3）。
    """
    from clipwright.schema.agent import FailureReport

    return FailureReport(
        # 与 PRD §5.4/A9 及 stage/pipeline 两处 capability 统一用 policy_refused
        category=category or "policy_refused",
        blocking_point=f"模型不可用：{key}",
        retryable="permanent",
    )


def _internal_failure(exc: Exception) -> Any:
    from clipwright.schema.agent import FailureReport

    name = type(exc).__name__.lower()
    category = "timeout" if "timeout" in name else (
        "model_incapable" if "llm" in name or "parse" in name else "internal_defect")
    return FailureReport(
        category=category,
        blocking_point=f"{type(exc).__name__}: {exc}"[:200],
        retryable="retry_same" if category != "internal_defect" else "needs_human",
    )


__all__ = [
    "agent_for",
    "get_stage",
    "list_stages",
    "reset",
    "run",
    "run_stage",
]

