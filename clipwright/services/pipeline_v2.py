"""Pipeline 编排器 v2 — 动态 Agent 路由 + 自愈循环 + 并行执行 + 超时熔断。

P0 修复:
 ・DAG 依赖关系修正 (material 不依赖 edit)
 ・_run_agent 不可达代码删除
P1 修复:
 ・asyncio.gather 多路错误聚合
 ・自愈循环联动重做 animation + audio
 ・全局管线超时 + 熔断
 ・LLM token 用量追踪
 ・Trace 事件隔离 (pipeline_id)
P2 修复:
 ・DAG 自动拓扑排序 (从 _DEPS 自动推导执行组)
 ・自愈上下文传递 (quality issues → redo agent)
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

from clipwright.agents import (
    AnimationAgent, AudioAgent, EditAgent,
    MaterialAgent, QualityAgent, StructureAgent,
)
from clipwright.category import CategoryRegistry
from clipwright.config import logger
from clipwright.persona.loader import load_persona_by_id, load_persona_or_default, resolve_inheritance
from clipwright.persona.validator import validate_manifest
from clipwright.schema.agent import AgentContext, AgentDecision
from clipwright.schema.pipeline import PipelineRequest, PipelineState, PipelineStatus, PipelineStep
from clipwright.schema.timeline import Timeline
from clipwright.services.agent_bus import AgentBus
from clipwright.services.trace import add_event, create_trace, format_tool_call
from clipwright.tool.registry import ToolRegistry

# ── 默认超时 ──────────────────────────────────────
# 动画阶段逐片段 LLM MG 生成（每个 2-4 分钟）是主要耗时来源；
# 默认给 30 分钟，前端/需求确认路径会按音频时长与场景数再叠加。
DEFAULT_PIPELINE_TIMEOUT_SEC = 1800  # 30 分钟


# ── 运行记录注册表 (Run Registry) ────────────────
# 无 Mongo 时记录管线执行历史，供 GET /api/pipeline/runs 消费；
# 有 Mongo 时优先读取持久化历史，内存记录覆盖当前进程内完成的运行。
_run_registry: list[dict] = []
_run_registry_lock = threading.Lock()

# G2: 协作式取消标记（per-pipeline）。
# 不强制中断 in-flight LLM 调用（asyncio.to_thread 不可取消），
# 在下一个 agent 边界（_dispatch 前）生效。
_CANCELLED: set[str] = set()


def mark_cancelled(pipeline_id: str) -> None:
    """标记管线取消（协作式）。"""
    _CANCELLED.add(pipeline_id)


def is_cancelled(pipeline_id: str) -> bool:
    return pipeline_id in _CANCELLED


def clear_cancel(pipeline_id: str | None = None) -> None:
    """清除取消标记（测试/重置用）。"""
    if pipeline_id is None:
        _CANCELLED.clear()
    else:
        _CANCELLED.discard(pipeline_id)


def _registry_snapshot() -> list[dict]:
    """深拷贝当前内存注册表（剔除内部时间戳字段）。"""
    with _run_registry_lock:
        return [{k: v for k, v in r.items() if k != "_start_dt"} for r in _run_registry]


def truncate_shared_data(shared: dict[str, Any], max_len: int = 5000) -> dict[str, Any]:
    """递归截断 shared_data 中的字符串字段，保留 dict/list 结构（B8）。

    - dict/list 值保持原结构，仅递归截断内部字符串字段；
    - 顶层标量字符串超长仍截断。
    """
    def _truncate(v: Any) -> Any:
        if isinstance(v, str):
            if len(v) > max_len:
                return v[:max_len] + f"...[截断, 原长{len(v)}]"
            return v
        if isinstance(v, dict):
            return {k: _truncate(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_truncate(item) for item in v]
        return v

    return {k: _truncate(v) for k, v in shared.items()}


def record_run_start(pipeline_id: str, topic: str) -> None:
    """在管线执行入口记录运行开始（status=running, agents 空）。"""
    now = datetime.now()
    with _run_registry_lock:
        _run_registry.append({
            "id": pipeline_id,
            "topic": topic or "",
            "status": "running",
            "duration_ms": 0,
            "started_at": now.isoformat(timespec="seconds"),
            "_start_dt": now,
            "agents": [],
        })
        # B9: 有界注册表——裁剪至最近 200 条，防止无界增长
        del _run_registry[:-200]


def record_run_complete(pipeline_id: str, status: str, steps: list[PipelineStep]) -> None:
    """在结果落库处更新运行结束（成功与失败分支共用）。

    把 PipelineStep 列表转换为前端期望的 agent 跨度
    ``[{agent, start, dur, status}]``。
    """
    spans: list[dict] = []
    with _run_registry_lock:
        for run in _run_registry:
            if run["id"] != pipeline_id:
                continue
            start_dt: Optional[datetime] = run.get("_start_dt")
            for s in steps or []:
                start_ms = 0
                if start_dt is not None and s.started_at is not None:
                    start_ms = max(0, int((s.started_at - start_dt).total_seconds() * 1000))
                span_status = "ok"
                if s.status == PipelineStatus.FAILED:
                    span_status = "fail"
                elif s.retry_count > 0:
                    span_status = "retry"
                spans.append({
                    "agent": s.agent_name,
                    "start": start_ms,
                    "dur": int(s.duration_ms or 0),
                    "status": span_status,
                })
            now = datetime.now()
            run["status"] = status
            run["duration_ms"] = int((now - start_dt).total_seconds() * 1000) if start_dt else 0
            run["agents"] = spans
            run.pop("_start_dt", None)
            return
        # 开始记录缺失（如进程内直接调用执行器）→ 补一条完成的记录
        _run_registry.append({
            "id": pipeline_id,
            "topic": "",
            "status": status,
            "duration_ms": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "agents": spans,
        })


def clear_run_records() -> None:
    """清空内存注册表（测试用）。"""
    with _run_registry_lock:
        _run_registry.clear()


def _as_same_tz(dt: datetime, ref: datetime) -> datetime:
    """把 dt 规整到与 ref 相同的时区，容忍 naive/aware 混用。"""
    if dt.tzinfo is None:
        if ref.tzinfo is not None:
            dt = dt.replace(tzinfo=ref.tzinfo)
    elif ref.tzinfo is not None:
        dt = dt.astimezone(ref.tzinfo)
    return dt


def _mongo_record_to_run(m: Any) -> Optional[dict]:
    """把 Mongo PipelineModel 文档映射为前端期望的 run 形状（防御式）。

    任一字段缺失/异常都返回 None，保证 Mongo 不可用时不影响内存记录。
    """
    try:
        request = getattr(m, "request", None) or {}
        topic = request.get("topic", "") if isinstance(request, dict) else ""
        created = getattr(m, "created_time", None)
        updated = getattr(m, "updated_time", None)
        if not isinstance(created, datetime) or not isinstance(updated, datetime):
            return None
        duration_ms = max(0, int((_as_same_tz(updated, created) - created).total_seconds() * 1000))
        spans: list[dict] = []
        for s in (getattr(m, "steps", None) or []):
            if not isinstance(s, dict):
                continue
            start_ms = 0
            try:
                s_start = s.get("started_at")
                if s_start:
                    start_ms = max(0, int(
                        (_as_same_tz(datetime.fromisoformat(str(s_start)), created) - created
                         ).total_seconds() * 1000))
            except (ValueError, TypeError):
                start_ms = 0
            span_status = "ok"
            if s.get("status") == "failed":
                span_status = "fail"
            elif s.get("retry_count", 0) > 0:
                span_status = "retry"
            spans.append({
                "agent": s.get("agent_name", ""),
                "start": start_ms,
                "dur": int(s.get("duration_ms") or 0),
                "status": span_status,
            })
        return {
            "id": str(m.id),
            "topic": topic,
            "status": getattr(m, "status", "completed") or "completed",
            "duration_ms": duration_ms,
            "started_at": created.isoformat(timespec="seconds"),
            "agents": spans,
        }
    except Exception as e:
        logger.debug("Mongo run 记录映射失败: %s", e)
        return None


def get_run_records(limit: int = 50) -> list[dict]:
    """获取运行记录（新→旧）。

    内存注册表始终返回（当前进程真实执行历史）；当 Mongo 可用时，
    再合并 PipelineModel 中的持久化历史（去重，内存优先）。
    """
    records = _registry_snapshot()
    try:
        from clipwright.context import mongo as mongo_ctx
        if mongo_ctx.is_connected:
            from clipwright.models.pipeline_model import PipelineModel
            memory_ids = {r["id"] for r in records}
            docs = PipelineModel.find_many({}, sort=[("created_time", -1)], limit=limit)
            for m in docs:
                if str(m.id) in memory_ids:
                    continue
                rec = _mongo_record_to_run(m)
                if rec:
                    records.append(rec)
    except Exception as e:
        logger.debug("GET /runs Mongo 读取失败，仅返回内存记录: %s", e)
    return records[:limit]


class AgentDAG:
    """Agent 依赖关系图 — 支持自动拓扑排序。

    用法:
        1. 编辑 _DEPS 添加/修改依赖关系
        2. get_execution_plan() 自动推导并行执行组
    """

    # Agent 依赖关系: {agent: [依赖的agent列表]}
    # 空列表 = 无依赖，可作为入口点
    _DEPS: dict[str, list[str]] = {
        "structure": [],           # 入口点，无依赖
        "material": ["structure"], # material 需要 structure 的 scenes 输出
        "edit": ["structure", "material"],
        "animation": ["edit"],
        "audio": ["animation"],     # Audio 依赖 Animation（防止并行覆盖时间轴）
        "quality": ["animation", "audio"],
    }

    @classmethod
    def get_execution_plan(cls) -> list[list[str]]:
        """从 _DEPS 自动拓扑排序 → 分阶段并行执行计划。

        quality 不参与 DAG 执行组：统一由 _run_inner 的自愈 while 循环调度
        （循环开头即运行 quality），避免同一管线内 quality 重复执行（B1）。
        但 _DEPS 仍保留 quality 依赖，供 _get_downstream_agents 自愈联动计算。

        Returns:
            [[stage1_agents], [stage2_agents], ...]
            同一阶段的 Agent 可以并行执行。
        """
        deps = {k: list(v) for k, v in cls._DEPS.items()}
        plan: list[list[str]] = []
        remaining = set(deps.keys()) - {"quality"}

        while remaining:
            # 找出所有依赖都已满足（或已在当前 plan 中）的 agent
            ready = []
            for agent in remaining:
                agent_deps = deps.get(agent, [])
                if all(d not in remaining for d in agent_deps):
                    ready.append(agent)

            if not ready:
                # 依赖环检测
                logger.warning("AgentDAG: 检测到依赖环，剩余: %s", remaining)
                # 把剩余的全部作为一组执行（可能失败，但比卡死好）
                ready = list(remaining)

            plan.append(ready)
            for agent in ready:
                remaining.remove(agent)

        logger.debug("AgentDAG 执行计划: %s", plan)
        return plan


class PipelineOrchestratorV2:
    """Pipeline 编排器 v2 — 支持动态路由、自愈循环、Agent 总线 + 并行执行。"""

    MAX_SELF_HEAL_LOOPS = 3
    # Agent 级熔断: agent_name → {"fail_count": int, "last_fail_at": datetime}
    # 类级变量：跨实例共享，确保熔断计数在多次 pipeline 运行间累积
    _circuit_breakers: dict[str, dict] = {}
    _circuit_breaker_threshold = 3
    _circuit_breaker_recovery_sec = 60

    def __init__(self) -> None:
        self._agents = {
            "structure": StructureAgent(),
            "material": MaterialAgent(),
            "edit": EditAgent(),
            "animation": AnimationAgent(),
            "audio": AudioAgent(),
            "quality": QualityAgent(),
        }

    def _check_circuit_breaker(self, agent_name: str) -> bool:
        """检查 agent 是否熔断。返回 True 表示已熔断（应跳过）。"""
        cb = self._circuit_breakers.get(agent_name)
        if not cb:
            return False
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        # 如果已过恢复期，重置熔断
        if cb["fail_count"] >= self._circuit_breaker_threshold:
            cb_last = cb.get("last_fail_at")
            if cb_last is not None:
                elapsed = (now - cb_last).total_seconds()
                if elapsed > self._circuit_breaker_recovery_sec:
                    cb["fail_count"] = 0
                    logger.info("熔断恢复: agent=%s (已过恢复期 %.0fs)", agent_name, elapsed)
                    return False
            logger.warning("Agent 熔断: %s (连续失败 %d 次)",
                          agent_name, cb["fail_count"])
            return True
        return False

    def _record_agent_failure(self, agent_name: str) -> None:
        """记录 agent 失败，更新熔断计数器。"""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        cb = self._circuit_breakers.setdefault(agent_name, {"fail_count": 0, "last_fail_at": None})
        cb["fail_count"] += 1
        cb["last_fail_at"] = now
        if cb["fail_count"] >= self._circuit_breaker_threshold:
            logger.warning("Agent 熔断触发: %s (连续失败 %d/%d 次)",
                          agent_name, cb["fail_count"], self._circuit_breaker_threshold)

    def _record_agent_success(self, agent_name: str) -> None:
        """记录 agent 成功，重置熔断计数器。"""
        cb = self._circuit_breakers.get(agent_name)
        if cb and cb["fail_count"] > 0:
            cb["fail_count"] = 0
            logger.info("Agent 熔断重置: %s", agent_name)

    async def run(
        self,
        request: PipelineRequest,
        pipeline_id: str = "",
        task_id: str = "",
    ) -> PipelineState:
        pid = pipeline_id or f"pl_v2_{uuid.uuid4().hex[:12]}"
        state = PipelineState(pipeline_id=pid, request=request)
        bus = AgentBus(pid)

        # Run registry: 记录运行开始（供 GET /api/pipeline/runs 消费）
        record_run_start(pid, request.topic)

        timeout_sec = request.extra_params.get("pipeline_timeout_sec", DEFAULT_PIPELINE_TIMEOUT_SEC)

        if not pipeline_id:
            create_trace(pid)

        # Layer 2: 初始化 SpanTracer
        from clipwright.services.tracing_service import SpanTracer
        tracer = SpanTracer(pid)
        root_span = tracer.start_span("pipeline", "system", f"管线: {request.topic[:50]}")

        # Layer 1: 持久化初始状态到 MongoDB（在线程池执行以免阻塞事件循环）
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._persist_state, state, "running")

        try:
            result = await asyncio.wait_for(
                self._run_inner(request, pid, state, bus, tracer, task_id),
                timeout=timeout_sec,
            )
            state = result
            error_category = "none"
            if state.status == PipelineStatus.COMPLETED:
                tracer.end_span(root_span, status="ok", output_summary=f"完成, 状态={state.status}")
            else:
                error_category = self._categorize_error(state.error or "")
                tracer.end_span(root_span, status="error", error=state.error)
        except asyncio.TimeoutError:
            state.status = PipelineStatus.FAILED
            state.error = f"管线执行超时（>{timeout_sec}s）"
            logger.error("PipelineV2 超时: %s", pid)
            error_category = "transient"
            tracer.end_span(root_span, status="error", error=state.error)
        except Exception as e:
            state.status = PipelineStatus.FAILED
            state.error = str(e)
            logger.exception("PipelineV2 执行异常: %s", e)
            error_category = "permanent"
            tracer.end_span(root_span, status="error", error=str(e))

        tracer.cleanup()
        state.updated_at = datetime.now()

        # G2: 协作式取消——若期间被标记取消，终态改为 CANCELLED 并写 trace/result
        if is_cancelled(pid):
            state.status = PipelineStatus.CANCELLED
            state.error = state.error or "管线已取消"
            add_event(pid, "system", "cancelled", "管线已取消")
            clear_cancel(pid)
            logger.info("PipelineV2 取消: %s", pid)

        # Layer 1: 持久化最终状态（在线程池执行）
        await loop.run_in_executor(None, self._persist_state, state, state.status.value, error_category)
        # Run registry: 记录运行结束（成功/失败共用，含 agent 跨度）
        record_run_complete(pid, state.status.value, state.steps)
        return state

    async def _self_heal_quality(
        self, state: PipelineState, result_data: dict, persona_config: dict, plugin,
        agent_context: AgentContext, bus: AgentBus, pid: str,
    ) -> bool:
        """质检 + 自愈循环（B3/B1 共用）：运行 quality，检出 error 则联动重做责任 Agent 及其下游。

        Returns:
            True 表示质检通过。
        """
        quality_passed = False
        heal_count = 0
        while not quality_passed and heal_count < self.MAX_SELF_HEAL_LOOPS:
            tl = result_data.get("timeline")
            if not tl:
                # 无时间线则无法质检——明确失败，避免静默跳过质检直接"完成"
                state.status = PipelineStatus.FAILED
                state.error = "质检阶段缺少时间线输入，无法完成质量校验"
                add_event(pid, "quality", "error", state.error)
                break
            step = await self._run_agent(
                state, "quality",
                {"timeline": tl, "constraints": persona_config.get("constraints", {})},
                agent_context, bus,
            )

            # 检查是否需要自愈
            has_errors, redo_agent, quality_issues = self._check_quality(step)
            if has_errors and redo_agent and heal_count < self.MAX_SELF_HEAL_LOOPS:
                heal_count += 1
                logger.info("自愈循环 [%d/%d]: → 重做 %s", heal_count, self.MAX_SELF_HEAL_LOOPS, redo_agent)
                add_event(pid, "system", "info",
                          f"自愈[{heal_count}]: 重做 {redo_agent}, 问题={len(quality_issues)}个")

                # P1: 将质检问题注入 agent_context 供重做时参考
                agent_context.extra_params["_quality_issues"] = quality_issues[:5]
                agent_context.extra_params["_quality_heal_count"] = heal_count

                # 重做指定的 Agent
                input_data = self._build_input(redo_agent, result_data, persona_config, plugin,
                                               extra_params=agent_context.extra_params)
                redo_step = await self._run_agent(
                    state, redo_agent, input_data, agent_context, bus
                )
                # 仅在重做成功时合并，避免失败的部分/空时间线覆盖好时间线
                if redo_step.result and getattr(redo_step, "status", None) != PipelineStatus.FAILED:
                    self._merge_agent_result(redo_agent, redo_step, result_data, bus, pid)

                # P1 FIX: 联动重做依赖于 redo_agent 的下游 agent
                downstream = self._get_downstream_agents(redo_agent)
                for dep_name in downstream:
                    if dep_name == "quality":
                        continue  # quality 由外层循环处理
                    add_event(pid, "system", "info",
                              f"自愈联动: 重做 {dep_name}（因 {redo_agent} 已重做）")
                    dep_input = self._build_input(dep_name, result_data, persona_config, plugin,
                                                  extra_params=agent_context.extra_params)
                    dep_step = await self._run_agent(
                        state, dep_name, dep_input, agent_context, bus
                    )
                    if dep_step and dep_step.result and getattr(dep_step, "status", None) != PipelineStatus.FAILED:
                        self._merge_agent_result(dep_name, dep_step, result_data, bus, pid)
            else:
                quality_passed = True

        # 清理注入的质检上下文
        agent_context.extra_params.pop("_quality_issues", None)
        agent_context.extra_params.pop("_quality_heal_count", None)
        return quality_passed

    async def run_from_agent(
        self,
        pipeline_id: str,
        request: PipelineRequest,
        agent_name: str,
        prior_state: Optional[dict] = None,
    ) -> PipelineState:
        """从指定 Agent 恢复执行（B3 retry）。

        result_data 重建算法（决策完备）：
        1. 从 prior_state.steps（按执行顺序）取 agent_name == 目标 之前所有
           ``status == completed`` 且 ``result`` 非空的步骤；
        2. 按 ``_merge_agent_result`` 的合并语义顺序重放——非控制键并入 result_data，
           timeline 取最后成功写入的 step.result 的 timeline dict（经 AgentBus）；
        3. 执行目标 agent + 下游联动（``_get_downstream_agents``），quality 走统一自愈循环；
        4. 目标 agent 无可用前置结果 / 无记录 → 抛 ValueError（端点映射 400）。
        """
        pid = pipeline_id or f"pl_v2_{uuid.uuid4().hex[:12]}"
        prior = prior_state or {}
        steps: list[dict] = prior.get("steps") or []
        if not steps:
            raise ValueError(f"Pipeline {pid} 无执行记录，无法 retry")

        # ── 1. 重建 result_data ──
        result_data: dict[str, Any] = {}
        bus = AgentBus(pid)
        target_index = None
        for i, s in enumerate(steps):
            if s.get("agent_name") == agent_name:
                target_index = i
                break
            if s.get("status") == "completed" and s.get("result"):
                fake_step = SimpleNamespace(
                    result=s.get("result"), agent_name=s.get("agent_name", ""),
                )
                self._merge_agent_result(s.get("agent_name", ""), fake_step, result_data, bus, pid)
        if target_index is None:
            raise ValueError(f"Pipeline {pid} 中未找到 Agent {agent_name}")
        if not result_data:
            raise ValueError(f"Pipeline {pid} 无可用前置成功结果，无法从 {agent_name} 恢复")

        # ── 2. 初始化（Persona/插件/上下文），与正常管线一致 ──
        state = PipelineState(pipeline_id=pid, request=request)
        manifest, plugin, persona_config, translated, agent_context = await self._init(
            request, pid, state, bus
        )

        # ── 3. 执行目标 agent ──
        add_event(pid, "system", "info", f"重试从 Agent {agent_name} 恢复")
        input_data = self._build_input(agent_name, result_data, persona_config, plugin,
                                       extra_params=agent_context.extra_params)
        target_step = await self._run_agent(state, agent_name, input_data, agent_context, bus)
        if target_step.result and getattr(target_step, "status", None) != PipelineStatus.FAILED:
            self._merge_agent_result(agent_name, target_step, result_data, bus, pid)

        # ── 4. 下游联动（传递闭包 + DAG 执行顺序；quality 由自愈循环处理）──
        # 直接依赖图的传递闭包：重做 edit 后，animation（依赖 edit）与 audio（依赖 animation）
        # 都需联动，而 _get_downstream_agents 只返回一层，故按依赖图 BFS 求全部下游。
        rev_deps: dict[str, set[str]] = {a: set() for a in AgentDAG._DEPS}
        for dep, base in AgentDAG._DEPS.items():
            for b in base:
                rev_deps.setdefault(b, set()).add(dep)
        downstream_set: set[str] = set()
        frontier = list(rev_deps.get(agent_name, set()))
        while frontier:
            cur = frontier.pop()
            if cur == "quality" or cur in downstream_set:
                continue
            downstream_set.add(cur)
            frontier.extend(rev_deps.get(cur, set()))
        plan_order = [a for group in AgentDAG.get_execution_plan() for a in group]
        for dep_name in plan_order:
            if dep_name == "quality" or dep_name not in downstream_set:
                continue
            add_event(pid, "system", "info",
                      f"retry 联动: 重做 {dep_name}（因 {agent_name} 已重做）")
            dep_input = self._build_input(dep_name, result_data, persona_config, plugin,
                                          extra_params=agent_context.extra_params)
            dep_step = await self._run_agent(state, dep_name, dep_input, agent_context, bus)
            if dep_step and dep_step.result \
                    and getattr(dep_step, "status", None) != PipelineStatus.FAILED:
                self._merge_agent_result(dep_name, dep_step, result_data, bus, pid)

        # ── 5. 质检 + 自愈（统一循环）──
        await self._self_heal_quality(state, result_data, persona_config, plugin,
                                      agent_context, bus, pid)

        # ── 6. 完成 ──
        state.shared_data["final_timeline"] = (
            bus.get_artifact("timeline").model_dump(mode="json")
            if bus.get_artifact("timeline")
            else None
        )
        _ft = state.shared_data.get("final_timeline")
        if _ft:
            add_event(pid, "system", "timeline_snapshot",
                      f"最终时间线: {len(_ft.get('tracks', []) or [])} 轨", _ft)
        if state.status != PipelineStatus.FAILED:
            state.status = PipelineStatus.COMPLETED
        state.updated_at = datetime.now()
        return state

    async def _run_inner(
        self, request: PipelineRequest, pid: str, state: PipelineState, bus: AgentBus,
        tracer: Any = None, task_id: str = "",
    ) -> PipelineState:
        """核心执行逻辑（被超时包裹）。"""
        # Layer 2/3: 更新任务队列进度
        if task_id:
            try:
                from clipwright.services.task_queue import get_task_queue
                get_task_queue().update_progress(task_id, 5, "加载 Persona 配置")
            except Exception:
                pass

        # 初始化
        manifest, plugin, persona_config, translated, agent_context = await self._init(
            request, pid, state, bus
        )
        result_data: dict[str, Any] = {}

        # Layer 2/3: 更新任务进度
        if task_id:
            try:
                from clipwright.services.task_queue import get_task_queue
                tq = get_task_queue()
                tq.update_progress(task_id, 10, f"执行 {len(AgentDAG.get_execution_plan())} 个阶段")
            except Exception:
                pass

        # P2: DAG 自动拓扑排序执行
        for group_idx, group in enumerate(AgentDAG.get_execution_plan()):
            logger.info("PipelineV2 执行组 [%d]: %s", group_idx, group)
            add_event(pid, "system", "info", f"执行组[{group_idx}]: {'+'.join(group)}")

            # Layer 2: 更新进度
            if task_id:
                try:
                    base_progress = 10 + group_idx * 15
                    get_task_queue().update_progress(task_id, base_progress, f"组[{group_idx}]: {'+'.join(group)}")
                except Exception:
                    pass

            tasks = {}
            for agent_name in group:
                input_data = self._build_input(agent_name, result_data, persona_config, plugin,
                                               extra_params=agent_context.extra_params)
                # Layer 2: span 在 _run_agent 内部创建（熔断后不创建 span）
                tasks[agent_name] = self._run_agent(
                    state, agent_name, input_data, agent_context, bus, tracer,
                )

            # P1: 并行执行 + 多路错误聚合
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

            errors: list[tuple[str, Exception]] = []
            for (name, _), result in zip(tasks.items(), results):
                if isinstance(result, Exception):
                    errors.append((name, result))
                    logger.error("并行 Agent %s 异常: %s", name, result)
                    add_event(pid, name, "error", f"{name} 异常: {str(result)[:200]}")
                    continue

                # 检查 agent 是否失败（用枚举直接比较——str(enum) 返回 'PipelineStatus.FAILED'
                # 而非 'failed'，旧的 str().lower() in (...) 永远匹配不上，导致失败被静默吞掉、
                # 管线仍报 COMPLETED 并产出空/残时间线）
                agent_failed = hasattr(result, "status") and result.status == PipelineStatus.FAILED
                if agent_failed:
                    err_msg = getattr(result, "error", None) or f"{name} 返回 FAIL"
                    # quality 失败不直接终止管线，交由后续自愈循环重做责任 Agent；
                    # 其它 Agent 失败则终止。
                    if name == "quality":
                        logger.info("Quality 检出问题，转入自愈循环处理: %s", err_msg)
                        add_event(pid, name, "warning", f"quality 检出问题: {str(err_msg)[:200]}")
                        continue
                    errors.append((name, Exception(err_msg)))
                    logger.error("Agent %s 返回 FAIL: %s", name, err_msg)
                    add_event(pid, name, "error", f"{name} 失败: {str(err_msg)[:200]}")
                    continue

                # 仅合并成功结果，避免失败 Agent 的部分/空时间线覆盖已累积的好时间线
                if hasattr(result, "result") and result.result:
                    self._merge_agent_result(name, result, result_data, bus, pid)

            # P1: 多路错误
            if errors:
                error_msgs = "; ".join(f"{n}: {e}" for n, e in errors)
                state.status = PipelineStatus.FAILED
                state.error = f"并行执行错误: {error_msgs}"
                for name, e in errors:
                    add_event(pid, name, "error", f"{name} 异常: {str(e)[:200]}")
                break

        if state.status == PipelineStatus.FAILED:
            state.updated_at = datetime.now()
            return state

        # ── 质检 + 自愈循环 (P1: 联动重做 animation + audio) ──
        await self._self_heal_quality(
            state, result_data, persona_config, plugin, agent_context, bus, pid,
        )

        # 清理注入的质检上下文
        agent_context.extra_params.pop("_quality_issues", None)
        agent_context.extra_params.pop("_quality_heal_count", None)

        # 完成
        state.shared_data["final_timeline"] = (
            bus.get_artifact("timeline").model_dump(mode="json")
            if bus.get_artifact("timeline")
            else None
        )

        # 与 V1 对齐：把最终时间线快照写入 trace，前端 SSE 据此进入审阅视图，
        # 不依赖 result 接口的轮询时机
        _ft = state.shared_data.get("final_timeline")
        if _ft:
            add_event(pid, "system", "timeline_snapshot",
                      f"最终时间线: {len(_ft.get('tracks', []) or [])} 轨",
                      _ft)

        if state.status != PipelineStatus.FAILED:
            state.status = PipelineStatus.COMPLETED

        state.updated_at = datetime.now()
        return state

    # ── 辅助方法 ──────────────────────────────────

    @staticmethod
    def _merge_agent_result(name: str, step: Any, result_data: dict, bus: AgentBus, pid: str) -> None:
        """合并 agent 结果。timeline 以总线为准。"""
        if not hasattr(step, "result") or not step.result:
            return

        # 非 timeline 字段正常合并（排除各 Agent 的控制字段，避免污染共享 result_data、
        # 掩盖更早的 error 或把 agent_name/decision 之类灌给下游 script_skeleton）
        _control_keys = {"timeline", "agent_name", "decision", "error", "status"}
        for k, v in step.result.items():
            if k not in _control_keys:
                result_data[k] = v

        # timeline 以总线为准
        tl_data = step.result.get("timeline")
        if tl_data:
            try:
                timeline = Timeline(**tl_data) if isinstance(tl_data, dict) else tl_data
                bus.set_artifact("timeline", timeline)
                # result_data 只存 dict 形式，不存 Timeline 对象
                result_data["timeline"] = timeline.model_dump(mode="json")
            except Exception as e:
                logger.warning("Timeline 合并失败 (%s): %s", name, e)

    @staticmethod
    def _check_quality(step: Any) -> tuple[bool, str, list[dict]]:
        """从 quality step 中提取自愈信息。"""
        if not step or not step.result:
            return False, "", []
        redo_agent = step.result.get("redo_agent", "")
        issues = step.result.get("issues", [])
        has_errors = any(
            isinstance(i, dict) and i.get("severity") == "error"
            for i in (issues or [])
        )
        return has_errors, redo_agent, issues or []

    @staticmethod
    def _get_downstream_agents(agent: str) -> list[str]:
        """获取依赖于此 agent 的所有下游（需联动重做）。"""
        return [
            a for a, deps in AgentDAG._DEPS.items()
            if agent in deps
        ]

    # ── 初始化 ────────────────────────────────────

    async def _init(self, request, pid, state, bus):
        """初始化：加载 Persona、插件、翻译参数。"""
        # Persona 不存在时回退到默认配置，避免整条管线因缺 Persona 而失败
        manifest = load_persona_or_default(request.persona_id)
        manifest = resolve_inheritance(manifest)
        warnings = validate_manifest(manifest)
        if warnings:
            state.shared_data["persona_warnings"] = warnings
        add_event(pid, "system", "info", f"加载 Persona: {request.persona_id}")

        plugin = CategoryRegistry.get(request.category_plugin_id)
        if plugin is None:
            raise ValueError(f"Unknown category plugin: {request.category_plugin_id}")

        persona_config = manifest.parameter.model_dump(mode="json") if manifest.parameter else {}
        translated = plugin.translate_persona(manifest.parameter) if manifest.parameter else {}

        # 加载 Persona 的 prompt.md 风格指引与知识库上下文，供 StructureAgent 使用，
        # 避免管线生成的脚本与需求阶段确认的风格发生漂移。
        self._persona_prompt = getattr(manifest, "prompt", "") or ""
        self._vision_prompt = getattr(manifest, "vision_prompt", "") or ""
        self._rag_context = ""
        try:
            from clipwright.rag.retriever import Retriever
            _ret = Retriever()
            _rag = await _ret.retrieve(persona_id=request.persona_id, query=request.topic or "")
            self._rag_context = _rag.context if _rag and _rag.context else ""
        except Exception as e:
            logger.debug("管线 RAG 检索失败: %s", e)

        agent_context = AgentContext(
            pipeline_id=pid,
            persona_id=request.persona_id,
            category_plugin_id=request.category_plugin_id,
            topic=request.topic,
            extra_params={
                **translated, **request.extra_params,
                "_persona_config": persona_config,
                "_identity": manifest.parameter.identity.model_dump(mode="json") if manifest.parameter and manifest.parameter.identity else {},
            },
        )

        bus.set_artifact("persona_config", persona_config)
        bus.set_artifact("plugin", plugin)
        bus.publish("system", "init", {
            "persona": request.persona_id,
            "plugin": request.category_plugin_id,
            "topic": request.topic,
        })

        return manifest, plugin, persona_config, translated, agent_context

    def _build_input(self, agent_name: str, result_data: dict, persona_config: dict, plugin,
                     extra_params: Optional[dict] = None) -> dict:
        """为 Agent 构建输入数据。"""
        # animation/audio/quality 依赖 edit 产出的时间线；若缺失则明确报错，
        # 避免下游以 None 时间线触发 Pydantic 校验崩溃或静默跳过质检。
        if agent_name in ("animation", "audio", "quality") and not result_data.get("timeline"):
            raise ValueError(f"Agent {agent_name} 需要时间线输入，但 edit 阶段未产出有效时间线")
        extra_params = extra_params or {}
        inputs = {
            "structure": {
                "persona_config": persona_config,
                "persona_prompt": getattr(self, "_persona_prompt", "") or result_data.get("persona_prompt", ""),
                "vision_prompt": getattr(self, "_vision_prompt", "") or result_data.get("vision_prompt", ""),
                "rag_context": getattr(self, "_rag_context", "") or result_data.get("rag_context", ""),
            },
            "material": {
                "script_skeleton": result_data,
                "persona_config": persona_config,
                "material_plugin_config": getattr(plugin, "config", {}) if plugin else {},
            },
            "edit": {
                "script_skeleton": result_data,
                "candidate_clips": result_data.get("candidate_clips", []),
            },
            "animation": {
                "timeline": result_data.get("timeline"),
                "visual_config": persona_config.get("visual", {}),
                "persona_prompt": getattr(self, "_persona_prompt", "") or result_data.get("persona_prompt", ""),
                "vision_prompt": getattr(self, "_vision_prompt", "") or result_data.get("vision_prompt", ""),
            },
            "audio": {
                "timeline": result_data.get("timeline"),
                "audio_config": {
                    **persona_config.get("audio", {}),
                    # B12: 优先透传前端 voice_id/auto_dub；未提供时回退 persona + 默认 auto_dub=True
                    "voice_id": extra_params.get("voice_id")
                    or persona_config.get("audio", {}).get("voice_clone_model_id")
                    or persona_config.get("audio", {}).get("voice")
                    or "",
                    "auto_dub": extra_params.get("auto_dub", True),
                },
            },
            "quality": {
                "timeline": result_data.get("timeline"),
                "constraints": persona_config.get("constraints", {}),
            },
        }
        return inputs.get(agent_name, {})

    # ── Agent 执行 ────────────────────────────────

    async def _run_agent(self, state, agent_name, input_data, context, bus,
                         tracer=None):
        """执行单个 Agent，集成总线通信。在熔断检查通过后创建 span。"""
        # 熔断检查 — 不创建 span
        if self._check_circuit_breaker(agent_name):
            step = state.add_step(agent_name)
            step.status = PipelineStatus.FAILED
            step.started_at = datetime.now()
            step.error = f"Agent {agent_name} 已熔断（连续失败 {self._circuit_breaker_threshold} 次），已跳过"
            step.completed_at = datetime.now()
            state.current_agent = agent_name
            add_event(state.pipeline_id, agent_name, "error", step.error)
            logger.warning("Agent[%s] 已熔断，跳过执行", agent_name)
            return step

        step = state.add_step(agent_name)
        step.status = PipelineStatus.RUNNING
        step.started_at = datetime.now()
        state.current_agent = agent_name
        pid = state.pipeline_id

        # 在熔断检查通过后创建 span
        agent_span_id = ""
        if tracer:
            agent_span_id = tracer.start_span(
                "agent", agent_name, f"Agent: {agent_name}",
                input_summary=str(list(input_data.keys())) if input_data else "",
            )

        def _end_span(status="ok", error="", metadata=None, output_summary=""):
            if tracer and agent_span_id:
                tracer.end_span(agent_span_id, status=status, error=error,
                                metadata=metadata, output_summary=output_summary)

        add_event(pid, agent_name, "agent_start", f"Agent: {agent_name}")
        logger.info("PipelineV2 Agent[%s] 开始", agent_name)

        # G2: 协作式取消——在 dispatch 前检查取消标记，命中则跳过该 agent
        if is_cancelled(pid):
            step.status = PipelineStatus.CANCELLED
            step.error = f"Agent {agent_name} 已跳过（管线取消）"
            step.completed_at = datetime.now()
            if step.started_at:
                step.duration_ms = int((step.completed_at - step.started_at).total_seconds() * 1000)
            add_event(pid, agent_name, "cancelled", f"{agent_name} 已跳过（管线取消）")
            _end_span(status="error", error=step.error)
            return step

        try:
            # 从总线获取其他 Agent 的需求
            demands = bus.get_demands()
            if demands:
                input_data["_demands"] = demands

            result = await self._dispatch(agent_name, input_data, context)
            step.result = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}

            if result.decision != AgentDecision.FAIL:
                step.status = PipelineStatus.COMPLETED
                self._record_agent_success(agent_name)
                _end_span(status="ok", error="",
                          output_summary=f"决策=PASS, keys={list(step.result.keys()) if step.result else []}")
            else:
                step.status = PipelineStatus.FAILED
                step.error = getattr(result, "error", None) or f"{agent_name} failed"
                self._record_agent_failure(agent_name)
                _end_span(status="error", error=step.error,
                          metadata={"error_category": self._categorize_error(step.error or "")})

            # 收集 LLM token 用量（从 Agent 对象属性读取，不在 model_dump 中）
            llm_usage = getattr(result, "_llm_usage", None)
            if llm_usage:
                step.result["llm_usage"] = llm_usage
                logger.info("Agent[%s] LLM tokens: input=%s, output=%s",
                            agent_name,
                            llm_usage.get("input_tokens", "?"),
                            llm_usage.get("output_tokens", "?"))
                try:
                    from clipwright.services.llm_tracker import record_llm_call
                    await record_llm_call(
                        pipeline_id=pid,
                        agent_name=agent_name,
                        model=llm_usage.get("model", "unknown"),
                        provider=context.extra_params.get("_llm_provider", "anthropic"),
                        input_tokens=llm_usage.get("input_tokens", 0),
                        output_tokens=llm_usage.get("output_tokens", 0),
                        duration_ms=step.duration_ms or 0,
                        prompt_summary=step.result.get("_prompt_summary", ""),
                        status="success" if result.decision != AgentDecision.FAIL else "error",
                    )
                except Exception:
                    pass

            # 发布到总线
            bus.publish(agent_name, "result", {
                "decision": str(result.decision),
                "keys": list(step.result.keys()) if step.result else [],
            })

            scenes = step.result.get("scenes", [])
            if scenes:
                bus.publish(agent_name, "scenes", {
                    "count": len(scenes),
                    "total_duration": sum(s.get("duration_sec", 0) for s in scenes),
                })

            add_event(pid, agent_name, "agent_end",
                      f"{agent_name} → {step.status} ({step.duration_ms or '?'}ms)")

            if agent_name == "edit":
                timeline_data = step.result.get("timeline")
                if timeline_data and isinstance(timeline_data, dict):
                    clips_info = []
                    for t in timeline_data.get("tracks", []):
                        for c in (t.get("clips", []) or []):
                            if c is None: continue
                            clips_info.append({
                                "kind": c.get("kind", ""),
                                "start": c.get("start_sec", 0),
                                "duration": c.get("duration_sec", 0),
                                "text": (c.get("text", "") or "")[:30],
                            })
                    bus.publish("edit", "clips", clips_info)

        except Exception as e:
            step.status = PipelineStatus.FAILED
            step.error = str(e)
            self._record_agent_failure(agent_name)
            add_event(pid, agent_name, "error", f"{agent_name} 异常: {str(e)[:200]}")
            logger.exception("Agent %s 异常: %s", agent_name, e)
            _end_span(status="error", error=str(e)[:200])

        step.completed_at = datetime.now()
        if step.started_at:
            step.duration_ms = int((step.completed_at - step.started_at).total_seconds() * 1000)
        state.updated_at = datetime.now()
        return step

    async def _dispatch(self, name: str, data: dict, ctx: AgentContext):
        """分发 Agent 调用。"""
        agent = self._agents[name]
        if name == "structure":
            from clipwright.schema.agent import StructureInput
            return await agent.execute(StructureInput(
                context=ctx,
                persona_config=data.get("persona_config", {}),
                persona_prompt=data.get("persona_prompt"),
                vision_prompt=data.get("vision_prompt"),
                rag_context=data.get("rag_context", ""),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        elif name == "material":
            from clipwright.schema.agent import MaterialInput
            return await agent.execute(MaterialInput(
                context=ctx,
                script_skeleton=data.get("script_skeleton", {}),
                persona_config=data.get("persona_config", {}),
                material_plugin_config=data.get("material_plugin_config", {}),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        elif name == "edit":
            from clipwright.schema.agent import EditInput
            return await agent.execute(EditInput(
                context=ctx,
                script_skeleton=data.get("script_skeleton", {}),
                candidate_clips=data.get("candidate_clips", []),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        elif name == "animation":
            tl = data.get("timeline")
            from clipwright.schema.agent import AnimationInput
            return await agent.execute(AnimationInput(
                context=ctx,
                timeline=tl,
                visual_config=data.get("visual_config", {}),
                persona_prompt=data.get("persona_prompt"),
                vision_prompt=data.get("vision_prompt"),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        elif name == "audio":
            from clipwright.schema.agent import AudioInput
            return await agent.execute(AudioInput(
                context=ctx,
                timeline=data.get("timeline"),
                audio_config=data.get("audio_config", {}),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        elif name == "quality":
            from clipwright.schema.agent import QualityInput
            return await agent.execute(QualityInput(
                context=ctx,
                timeline=data.get("timeline"),
                constraints=data.get("constraints", {}),
                creative_brief=ctx.extra_params.get("creative_brief"),
                production_plan=ctx.extra_params.get("production_plan"),
            ), ctx)
        raise ValueError(f"Unknown agent: {name}")

    # ── Layer 1: 持久化 + 错误分级 ───────────────

    def _persist_state(self, state: PipelineState, status: str = "", error_category: str = "") -> None:
        """持久化管线状态到 MongoDB（含截断保护）。"""
        try:
            from clipwright.context import mongo as mongo_ctx
            if not mongo_ctx.is_connected:
                return
            from clipwright.models.pipeline_model import PipelineModel
            # 截断过大的 shared_data 字段，避免 MongoDB 16MB 限制。
            # B8：递归截断内部字符串并保留 dict/list 结构，不再把容器 str() 成字符串。
            truncated_shared = truncate_shared_data(state.shared_data or {})
            data = {
                "status": status or state.status.value,
                "request": state.request.model_dump(mode="json") if hasattr(state.request, "model_dump") else {},
                "steps": [s.model_dump(mode="json") for s in state.steps] if state.steps else [],
                "shared_data": truncated_shared,
                "final_timeline": state.shared_data.get("final_timeline"),
                "output_path": state.output_path or "",
                "error": state.error or "",
                "error_category": error_category,
                "duration_sec": 0,
            }
            # 保存 extra_params 到独立的字段中，便于查询
            if hasattr(state, 'request') and state.request:
                ep = getattr(state.request, 'extra_params', {})
                if ep:
                    # 安全截取关键参数并存为字符串
                    data["extra_params_summary"] = {k: str(v)[:200] for k, v in ep.items()}
            model = PipelineModel.find_by_id(state.pipeline_id)
            if model:
                for k, v in data.items():
                    setattr(model, k, v)
                model.update()
            else:
                PipelineModel(_id=state.pipeline_id, **data).insert()
        except Exception as e:
            logger.warning("Pipeline 持久化失败: %s", e)

    @staticmethod
    def _categorize_error(error: str) -> str:
        """对错误进行分类: transient / permanent / fatal。

        - transient: 可重试（LLM 超时、网络波动）
        - permanent: 不可重试（参数错误、类型不匹配）
        - fatal: 系统级（内存不足、数据库断开）——A7 落地（文档承诺 fatal 分类）
        """
        transient_patterns = ["timeout", "超时", "rate limit", "too many", "暂时", "retry",
                              "connection", "reset", "timed out"]
        permanent_patterns = ["not found", "unknown", "invalid", "type", "格式错误",
                              "NoneType", "AttributeError", "KeyError"]
        fatal_patterns = ["memoryerror", "out of memory", "oom", "mongo", "not connected",
                          "disk full", "磁盘", "database"]
        lowered = error.lower()
        for p in transient_patterns:
            if p in lowered:
                return "transient"
        for p in fatal_patterns:
            if p in lowered:
                return "fatal"
        for p in permanent_patterns:
            if p in lowered:
                return "permanent"
        return "permanent"

    @staticmethod
    def _should_stop(state: PipelineState, step: PipelineStep) -> bool:
        if step.status in (PipelineStatus.FAILED, PipelineStatus.CANCELLED):
            state.status = PipelineStatus.FAILED
            state.error = step.error or f"Agent {step.agent_name} failed"
            return True
        return False
