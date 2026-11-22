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
import json
import time
from typing import Any, Iterable, Optional

from clipwright.config import logger, settings
from clipwright.schema.agent import AgentDecision
from clipwright.schema.run import AgentResult
from clipwright.schema.stage import (
    STAGE_NAMES,
    TIMELINE_STAGES,
    StageSpec,
)
from clipwright.services import stage_inputs, stage_merge, task_blackboard as bb
from clipwright.services.task_events import emit

#: 阶段 → Agent 类（延迟导入：这些 Agent 各自会 import 一堆重量级依赖）
_STAGE_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "requirements": ("clipwright.agents.requirements_agent", "RequirementsAgent"),
    "structure": ("clipwright.agents.structure_agent", "StructureAgent"),
    "material": ("clipwright.agents.material_agent", "MaterialAgent"),
    "edit": ("clipwright.agents.edit_agent", "EditAgent"),
    "animation": ("clipwright.agents.animation_agent", "AnimationAgent"),
    "audio": ("clipwright.agents.audio_agent", "AudioAgent"),
    "quality": ("clipwright.agents.quality_agent", "QualityAgent"),
}

#: 进程内运行表：task_id → [StageResult]
def _stage_result(*, task_id: str, spec: Any, stage: str, status: str,
                 artifacts: dict[str, Any] | None = None, summary: str = "",
                 writes: list[str] | None = None,
                 degradations: list[dict[str, Any]] | None = None,
                 issues: list[dict[str, Any]] | None = None,
                 redo_agent: str = "", redo_hint: str = "",
                 needs: list[dict[str, Any]] | None = None,
                 details: dict[str, Any] | None = None,
                 cost_usd: float | None = None, cost_unknown: bool = False,
                 tokens: int = 0,
                 tool_calls: list[dict[str, Any]] | None = None,
                 tool_calls_path: str = "", duration_ms: int = 0,
                 provider: str = "", model: str = "",
                 failure: Any = None, error: str = "",
                 report_path: str = "", report_seq: int = 0,
                 attempt: int | None = None) -> AgentResult:
    """B21b：阶段终态直接构造成 AgentResult（唯一铁厂是 result_assembler）。"""
    from clipwright.services import result_assembler as _ra

    return _ra.build(
        run_id=str(getattr(spec, "run_id", "") or f"{task_id}:{stage}"),
        kind="stage", mode="task",
        task_id=str(task_id), trace_id=str(getattr(spec, "trace_id", "") or ""),
        attempt=int(attempt or getattr(spec, "attempt", 1) or 1),
        stage=str(stage), role=str(stage), status=status,  # type: ignore[arg-type]
        summary=summary, outputs=dict(artifacts or {}),
        writes=list(writes or []), needs=list(needs or []),
        failure=failure, error=error,
        issues=list(issues or []), redo_agent=redo_agent, redo_hint=redo_hint,
        degradations=list(degradations or []), report_path=report_path,
        report_seq=report_seq, tool_calls=list(tool_calls or []),
        tool_calls_path=tool_calls_path, tokens=tokens,
        cost_usd=cost_usd, cost_unknown=cost_unknown, duration_ms=duration_ms,
        provider=provider, model=model, details=dict(details or {}),
    )



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
        raise ValueError(f"阶段 {stage} 没有对应的 Agent（未知阶段）")
    module_name, class_name = dep
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)()


def get_stage(task_id: str, stage: str) -> Optional[dict[str, Any]]:
    """最近一次该阶段的 run（B21b：读 RunStore，不再有进程内镜像）。"""
    from clipwright.services import run_store

    for result in reversed(run_store.list_task(str(task_id))):
        if str(getattr(result, "stage", "")) == str(stage):
            return result.model_dump(mode="json")
    return None


def reset(task_id: str = "") -> None:
    """兼容旧测试：进程内运行表已删除，RunStore 由测试用 tmp workspace 隔离。"""
    return None


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


def _stage_progress_counts(task_id: str) -> tuple[int, int]:
    """(已完成阶段数, 计划阶段数)。

    B08：完成数来自**本任务已提交的 run 收据**（ok/degraded，且属于计划阶段），
    不再数“工作区里有多少份报告”——项目工作区可能被多个任务共用，按报告数会把
    别的任务的阶段算进来；requirements 也不在 6 阶段分母里。
    """
    try:
        from clipwright.services import run_store

        runs = run_store.list_task(str(task_id))
        done = {
            str(r.stage) for r in runs
            if str(r.kind) == "stage" and str(r.stage) in PLAN_STAGES
            and str(r.status) in ("ok", "degraded")
        }
        return len(done), len(PLAN_STAGES)
    except Exception:  # noqa: BLE001 — 统计失败按"总数未定"处理
        return 0, 0


#: 计划里的六个阶段（进度分母；主 agent 临时加派不改变分母，播报里会写"计划已调整"）
PLAN_STAGES: tuple[str, ...] = ("structure", "material", "edit", "animation",
                                "audio", "quality")


def _explicit_timeline_for_redo(task: Any, stage: str, explicit: dict,
                                refs: list[str] | None = None) -> dict | None:
    """重做上游阶段时，从显式 inputs 里解析出可回写的 timeline。

    只在调用方真的带了 timeline 输入时生效；没有就不猜（避免把错误时间线
    当作起点）。引用形如 {"artifact": "art_x", "path": "artifacts/art_x.json"}。
    """
    if stage not in ("edit", "animation"):
        return None
    raw = (explicit or {}).get("timeline")
    if not isinstance(raw, dict) and refs:
        # 主 Agent 有时把时间线放在 input_refs（artifacts/art_x.json），而不是
        # inputs.timeline。重做 animation 时按引用里第一个 timeline 回退。
        root0 = _workspace_root_of(task)
        if root0:
            for rel in refs:
                try:
                    cand = json.loads((Path(root0) / str(rel)).read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(cand, dict) and isinstance(cand.get("tracks"), list):
                    raw = {"path": str(rel)}
                    break
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("tracks"), list):
        return raw
    root = _workspace_root_of(task)
    if not root:
        return None
    rel = str(raw.get("path") or "")
    art = str(raw.get("artifact") or "")
    if not rel and art:
        rel = f"artifacts/{art}.json"
    if not rel:
        return None
    try:
        from pathlib import Path as _Path
        path = _Path(root) / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("tracks"), list) else None
    except Exception:  # noqa: BLE001 — 引用坏了就不回退，交给装配校验
        return None


async def run_stage(task: Any, spec: StageSpec) -> AgentResult:
    """真跑一个阶段，返回结构化结果（并合并进黑板）。"""
    task_id = str(getattr(task, "id", "") or "")
    stage = str(spec.stage)
    # 显式 inputs 时**不读黑板来"补齐必需键"**（PRD §16.2 / S2：数据必须过主 agent 的手），
    # 但空值回退需要黑板（见 stage_inputs.assemble 的 _flow_keys）——二者不冲突：
    # 校验只认显式键，取值时空值才回退。
    explicit = dict(spec.inputs or {})
    # CAS 基线：阶段跑完后如果黑板已被别的写入推进，提交必须拒绝而不是覆盖
    board_revision = bb.revision(task_id)
    board = bb.get(task_id)
    # 重做 animation/edit 时，如果黑板 timeline 的 owner 是下游（audio），
    # 先把它回退到这次显式传入的上游时间线，否则 animation 会被
    # “只许下游覆盖上游”守卫拒绝，随后 audio 又拿旧时间线覆盖动画层。
    try:
        _redo_tl = _explicit_timeline_for_redo(
            task, stage, explicit,
            list((spec.args or {}).get("input_refs") or []),
        )
        _owner = bb.owner_of(task_id, "timeline")
        if _redo_tl is not None and _owner:
            _rank = bb.TIMELINE_STAGE_ORDER
            if _rank.get(_owner, 99) > _rank.get(stage, 99):
                bb.reset_timeline(
                    task_id, _redo_tl,
                    by="edit" if stage == "animation" else stage,
                    reason=f"redo {stage}: 黑板 timeline owner={_owner}，回退到显式输入的上游时间线",
                )
                board = bb.get(task_id)
                emit(task_id, "narration",
                     f"黑板 timeline owner 从 {_owner} 回退到 {stage} 的上游起点（重做用）",
                     {"kind": "timeline_owner_reset", "from": _owner, "stage": stage},
                     span="kernel")
                logger.warning("阶段 %s 重做：timeline owner %s → upstream（显式输入回退）",
                               stage, _owner)
    except Exception as _reset_err:  # noqa: BLE001 — 回退失败不阻断，交给黑板守卫给出可读错误
        logger.warning("阶段 %s 重做：timeline owner 回退失败: %s", stage, _reset_err)
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
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage,
            status="failed",
            error=str(exc),
            summary=f"缺输入：{'、'.join(exc.missing)}",
            needs=_missing_needs(exc),
            failure=_missing_failure(stage, exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        emit(task_id, "step_end", f"阶段 {stage} 无法开始：{result.error}",
             {"stage": stage, "status": result.status,
              "missing": exc.missing, "suggestion": exc.suggestion})
        return result
    except Exception as exc:  # noqa: BLE001 — 装配异常同样要如实回报
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage, status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    if spec.dry_run:
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage, status="dry_run",
            summary=f"输入已装配（{type(input_obj).__name__}），未执行",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    # 2) 路由：显式 provider/model 只校验「已配置 provider + 模型可用」，
    #    不受 model_whitelist 阻断（2026-10-28 PRD B3）；解析失败如实拒绝，
    #    绝不静默回退默认模型（用户以为派了 X、实际跑默认模型属于欺骗）。
    route = None
    if spec.provider or spec.model:
        from clipwright.services.model_router import resolve_explicit_route

        resolved = resolve_explicit_route(spec.provider, spec.model)
        if not resolved.ok:
            result = _stage_result(task_id=task_id, spec=spec, 
                stage=stage, status="refused",
                error=f"显式模型不可用：{resolved.message}",
                failure=_policy_failure(
                    f"{spec.provider or '<默认>'}/{spec.model or '<默认>'}",
                    category=resolved.reason or "provider_not_configured",
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
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
    skill_calls: list[dict[str, Any]] = []
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

            # 技能轮（skill-pluginization M2）：没有 LLM 工具轮的阶段（material /
            # animation / audio / quality 等）先让模型自主选择 0–2 个技能补分析；
            # 有工具轮的阶段（edit 等）技能已并入工具面，这里仍可补一次轻量调用。
            try:
                if getattr(agent, "run_skill_round", None) is not None:
                    _brief = f"{stage} 阶段：{str(getattr(spec, 'brief', '') or '')[:200]}"
                    _sr = await asyncio.wait_for(
                        agent.run_skill_round(_brief, pipeline_id=context.pipeline_id),
                        timeout=min(60.0, timeout_eff) if timeout_eff > 0 else 60.0,
                    )
                    skill_calls = list((_sr or {}).get("tool_calls") or [])
            except Exception as exc:  # noqa: BLE001 — 技能轮失败不影响阶段主流程
                logger.debug("阶段 %s 技能轮跳过: %s", stage, exc)

            # CAS 基线必须在所有“执行前写入”（如 reset_timeline）之后取：
            # 否则阶段自己的回退会被当成并发冲突。
            board_revision = bb.revision(task_id)
            # 断点续跑：material 的输入摘要只取结构产物（不带输出键），
            # 这样第二次重派（黑板已多出 candidate_clips）仍能命中同一检查点。
            if stage == "material" and isinstance(input_obj, dict) is False:
                try:
                    from clipwright.services import checkpoints as _ckpt

                    _root = str(_workspace_root_of(task) or "")
                    if _root:
                        _digest = _ckpt.digest({
                            "stage": stage,
                            "script_skeleton": board.get("script_skeleton"),
                            "scenes": board.get("scenes"),
                        })
                        _cp = _ckpt.load(_root, stage, _digest)
                        if hasattr(input_obj, "resume_checkpoint"):
                            input_obj.resume_checkpoint = (
                                _cp.model_dump(mode="json") if _cp is not None
                                else {"input_digest": _digest, "partial_outputs": []}
                            )
                except Exception as exc:  # noqa: BLE001 — 检查点坏了按全新跑
                    logger.warning("阶段 %s 读取检查点失败（按全新跑）: %s", stage, exc)
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
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage, status="failed",
            error=f"阶段超时（>{timeout_eff}s）",
            failure=_timeout_failure(stage, timeout_eff),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        emit(task_id, "step_end", f"阶段 {stage} 超时", {"stage": stage, "status": "failed"})
        return result
    except asyncio.CancelledError:
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage, status="cancelled", error="阶段被取消",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:  # noqa: BLE001 — 阶段失败不能带崩 Kernel
        logger.warning("阶段 %s 执行失败 %s: %s", stage, task_id, exc)
        result = _stage_result(task_id=task_id, spec=spec, 
            stage=stage, status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
            failure=_internal_failure(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
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
    stage_tool_calls = list(skill_calls) + list(getattr(agent, "last_tool_calls", None) or [])
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
            writes, merge_errors = stage_merge.merge(
                stage, payload, task_id=task_id, expect_revision=board_revision,
            )
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

    # attempt = 该阶段在本任务里跑过几次（含本次）：进度去重键与报告文件名都用它
    from clipwright.services import run_store as _run_store
    attempt_no = sum(1 for r in _run_store.list_task(task_id)
                     if str(getattr(r, "stage", "")) == stage) + 1

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

    _summary = _summary_of(stage, payload, tool_calls=stage_tool_calls)
    _failed_anims = list(payload.get("issues") or []) if stage == "animation" else []
    if _failed_anims:
        _summary += f"；{len(_failed_anims)} 段动画失败，主 Agent 必须按 clip_id 重派（不得收据收尾）"
    result = _stage_result(task_id=task_id, spec=spec, 
        stage=stage,
        status=status,
        artifacts={k: payload[k] for k in ("timeline", "script_skeleton",
                                           "candidate_clips", "issues") if payload.get(k)},
        summary=_summary,
        writes=writes,
        degradations=_degradations,
        issues=list(payload.get("issues") or [])[:20],
        redo_agent=str(payload.get("redo_agent") or ""),
        redo_hint=str(payload.get("redo_hint") or ""),
        needs=[n for n in (payload.get("needs") or []) if isinstance(n, dict)],
        details={
            k: payload.get(k) for k in (
                "animation_plan", "generated_mg_count", "material_notes", "edit_notes",
                "audio_notes", "audio_config_internal", "passed", "fix_suggestions", "score",
                "redo_request",
            ) if payload.get(k) not in (None, "", [], {})
        } | {
            # 输入快照（B05）：执行前黑板代数 + 摘要；提交被 CAS 拒绝时据此定位
            "input_revision": board_revision,
            "input_digest": _board_digest(board),
            "output_revision": bb.revision(task_id),
            **({"merge_errors": merge_errors} if merge_errors else {}),
        },
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

        # 产物先落盘再写报告：引用里只带 preview，大对象留给 read_artifact 按需读
        try:
            result.outputs = persist_artifacts(root, attempt_no, result.outputs, stage=stage)
        except Exception as exc:  # noqa: BLE001 — 落盘失败保持内联，不阻断
            logger.warning("产物落盘失败 %s/%s: %s", task_id, stage, exc)
        body = stage_report.build_body(
            payload, stage=stage, status=status, summary=result.summary,
            needs=[n for n in (payload.get("needs") or []) if isinstance(n, dict)],
            degradations=_degradations, issues=result.issues,
            artifact_names=list((payload.get("artifacts") or {}).keys()) if isinstance(
                payload.get("artifacts"), dict) else list(result.outputs.keys()),
        )
        path, seq = "", 0
        try:
            path, seq = stage_report.write_report(
                workspace_root=root, stage=stage, status=status, attempt=attempt_no,
                body=body, artifacts=_artifact_refs(result.outputs), cost_usd=cost,
                duration_ms=result.duration_ms, provider=result.provider, model=result.model,
                task_id=task_id, run_id=str(getattr(spec, "run_id", "") or ""),
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
                run_id=str(getattr(spec, "run_id", "") or ""),
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
            try:
                _bytes = Path(path).stat().st_size
            except OSError:
                _bytes = 0
            emit(task_id, "doc_write", f"报告已写：{path}",
                 {"path": path, "bytes": _bytes, "by": stage, "seq": seq,
                  "run_id": str(getattr(spec, "run_id", "") or ""),
                  "status": result.status})

    # B21b：报告/轨迹路径是构造后才知道的，最后按真实字段重建一次（report_state/
    # handoff_summary 由 assembler 依据最终 report_path 计算，避免留下 stale 状态）。
    result = _stage_result(
        task_id=task_id, spec=spec, stage=stage, attempt=attempt_no,
        status=result.status, artifacts=result.outputs, summary=result.summary,
        writes=writes, degradations=list(result.degradations), issues=result.issues,
        redo_agent=result.redo_agent, redo_hint=result.redo_hint,
        needs=list(result.needs), details=result.details,
        cost_usd=cost, cost_unknown=cost_unknown, tokens=tokens,
        tool_calls=stage_tool_calls, tool_calls_path=result.tool_calls_path,
        duration_ms=result.duration_ms, provider=result.provider, model=result.model,
        failure=result.failure, error=result.error,
        report_path=result.report_path, report_seq=result.report_seq,
    )
    emit(task_id, "step_end", f"阶段 {stage} → {result.status}：{result.summary}"[:200],
         {"stage": stage, "status": result.status, "writes": writes,
          "cost_usd": cost, "duration_ms": result.duration_ms,
          "summary": result.summary, "report_path": result.report_path,
          "report_seq": result.report_seq,
          "report_state": "written" if result.report_path else "unavailable",
          "run_id": str(getattr(spec, "run_id", "") or ""),
          "input_revision": board_revision})
    return result


def _dump_tool_calls(root: str, *, stage: str, attempt: int,
                     calls: list[dict[str, Any]], run_id: str = "") -> str:
    """把阶段全量工具轨迹写成 JSON（`reports/tool_calls/<stage>-<attempt>.json`）。

    文件名暂保持 `stage-attempt`（B10 重写 trace API 时切到 run_id 寻址）；
    payload 里必须带 `run_id`，否则跨任务/重放的轨迹无法归属。
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
        "run_id": str(run_id or ""),
        "count": len(calls),
        "calls": calls,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def _board_digest(board: dict[str, Any]) -> str:
    """黑板快照摘要（只用于定位“这次是基于哪版输入跑的”，不参与业务判断）。"""
    try:
        import hashlib

        raw = json.dumps(board, ensure_ascii=False, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — 摘要失败不影响阶段
        return ""


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


def _artifact_refs(outputs: dict[str, Any] | None) -> dict[str, Any]:
    """把产物整理成报告里的引用表（大对象只留指针 + preview）。"""
    out: dict[str, Any] = {}
    for name, value in (outputs or {}).items():
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

    # 每个缺失键一条 needs（旧实现按"建议上游阶段"聚合、并把字段用「、」拼成一个
    # 字符串，主 Agent 看到 field='script_skeleton、candidate_clips' 无法逐项补齐）。
    return FailureReport(
        category="input_insufficient",
        blocking_point=str(exc)[:200],
        retryable="needs_upstream",
        evidence=[{"kind": "missing_keys", "text": "、".join(exc.missing)}],
    )


def _missing_needs(exc: stage_inputs.StageInputMissing) -> list[dict[str, Any]]:
    """缺项 → v2 needs（每个缺失字段一条，不拼串、不聚合）。"""
    fallback_stage = (exc.suggestion or ["structure"])[0]
    out = [
        {
            "field": str(field),
            "from_stage": stage_inputs.KEY_PRODUCER.get(str(field), fallback_stage),
            "why": f"阶段必需输入缺失：{field}",
            "resolution": "upstream",
        }
        for field in (exc.missing or [])
    ]
    if out:
        return out
    return [{
        "field": "、".join(exc.missing) or "input",
        "from_stage": s,
        "why": "阶段必需输入缺失",
        "resolution": "upstream",
    } for s in (exc.suggestion or ["structure"])]


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
    category = str(getattr(exc, "category", "") or "")
    if not category:
        category = "timeout" if "timeout" in name else (
            "model_incapable" if "llm" in name or "parse" in name else "internal_defect")
    return FailureReport(
        category=category,
        blocking_point=f"{type(exc).__name__}: {exc}"[:200],
        retryable=("retry_same" if category in ("timeout", "model_incapable", "provider_error")
                   else "needs_human"),
        evidence=[{"kind": "provider_error", "text": str(exc)[:200]}] if category == "provider_error" else [],
    )


__all__ = [
    "agent_for",
    "get_stage",
    "reset",
    "run_stage",
]

