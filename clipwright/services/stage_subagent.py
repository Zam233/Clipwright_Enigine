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
DEFAULT_STAGE_TIMEOUT_LOCAL = 300.0


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


def _summary_of(stage: str, payload: dict[str, Any]) -> str:
    """给主 Agent 的一句话结论（关键数值不许丢）。"""
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


async def run_stage(task: Any, spec: StageSpec) -> StageResult:
    """真跑一个阶段，返回结构化结果（并合并进黑板）。"""
    task_id = str(getattr(task, "id", "") or "")
    stage = str(spec.stage)
    # 显式 inputs 时**不读黑板**（PRD §16.2 / S2：数据必须过主 agent 的手）
    explicit = dict(spec.inputs or {})
    board = {} if explicit else bb.get(task_id)
    started = time.monotonic()

    emit(task_id, "step_start", f"阶段子代理：{stage}",
         {"stage": stage, "args_keys": sorted((spec.args or {}).keys()),
          "inputs_keys": sorted(explicit.keys()),
          "inputs_source": "explicit" if explicit else "blackboard",
          "blackboard_keys": sorted(board.keys())})

    # 1) 装配输入
    try:
        if explicit:
            _needs = stage_inputs.validate(stage, explicit)
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

    # 2) 路由（含白名单校验：越权立即拒绝，不静默回退）
    route = None
    if spec.provider or spec.model:
        from clipwright.services.model_router import ModelRoute, is_route_allowed

        route = ModelRoute(provider=spec.provider, model=spec.model)
        if not is_route_allowed(route):
            result = StageResult(
                stage=stage, status="refused",
                error=f"模型不在白名单：{route.key}",
                failure=_policy_failure(route.key),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            _record(task_id, result)
            return result

    # 3) 执行
    from clipwright.services.degradation import degradation_scope
    from clipwright.services.llm import llm_usage_snapshot
    from clipwright.services.model_router import use_route

    context = stage_inputs.build_context(task)
    usage_before = llm_usage_snapshot()
    degradations: list[Any] = []
    payload: dict[str, Any] = {}
    try:
        agent = agent_for(stage)
        with degradation_scope(degradations), use_route(route):
            output = await asyncio.wait_for(
                agent.execute(input_obj, context), timeout=float(spec.timeout_sec or 900.0),
            )
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
            error=f"阶段超时（>{spec.timeout_sec}s）",
            failure=_timeout_failure(stage, spec.timeout_sec),
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
    status = "ok"
    if failure is not None:
        status = "failed"
    elif merge_errors:
        # 合并失败（例如时间线没能落黑板）必须降级为 degraded：
        # 旧实现只打 warning，阶段报 ok 但下游拿不到 timeline，故障在下游才炸。
        status = "degraded"
    elif payload.get("degradations") or (payload.get("decision") == "pass" and writes == []
                                        and stage in TIMELINE_STAGES):
        status = "degraded"

    _degradations = [d if isinstance(d, dict) else
                     getattr(d, "model_dump", lambda **_: {})()
                     for d in (payload.get("degradations") or [])][:20]
    _degradations += [{"kind": "blackboard_merge_failed", "detail": e,
                       "scope": stage, "impact": "下游阶段可能拿不到该产物"}
                      for e in merge_errors[:5]]

    result = StageResult(
        stage=stage,
        status=status,
        artifacts={k: payload[k] for k in ("timeline", "script_skeleton",
                                           "candidate_clips", "issues") if payload.get(k)},
        summary=_summary_of(stage, payload),
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


def _policy_failure(key: str) -> Any:
    from clipwright.schema.agent import FailureReport

    return FailureReport(
        # 与 PRD §5.4/A9 及 stage/pipeline 两处 capability 统一用 policy_refused
        category="policy_refused",
        blocking_point=f"模型不在白名单：{key}",
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
