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
import uuid
from datetime import datetime
from typing import Any, Optional

from clipwright.agents import (
    AnimationAgent, AudioAgent, EditAgent,
    MaterialAgent, QualityAgent, StructureAgent,
)
from clipwright.category import CategoryRegistry
from clipwright.config import logger
from clipwright.persona.loader import load_persona_by_id, resolve_inheritance
from clipwright.persona.validator import validate_manifest
from clipwright.schema.agent import AgentContext, AgentDecision
from clipwright.schema.pipeline import PipelineRequest, PipelineState, PipelineStatus, PipelineStep
from clipwright.schema.timeline import Timeline
from clipwright.services.agent_bus import AgentBus
from clipwright.services.trace import add_event, create_trace, format_tool_call
from clipwright.tool.registry import ToolRegistry

# ── 默认超时 ──────────────────────────────────────
DEFAULT_PIPELINE_TIMEOUT_SEC = 900  # 15 分钟


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
        "audio": ["edit"],         # Audio 和 Animation 可并行
        "quality": ["animation", "audio"],
    }

    @classmethod
    def get_execution_plan(cls) -> list[list[str]]:
        """从 _DEPS 自动拓扑排序 → 分阶段并行执行计划。

        Returns:
            [[stage1_agents], [stage2_agents], ...]
            同一阶段的 Agent 可以并行执行。
        """
        deps = {k: list(v) for k, v in cls._DEPS.items()}
        plan: list[list[str]] = []
        remaining = set(deps.keys())

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

    def __init__(self) -> None:
        self._agents = {
            "structure": StructureAgent(),
            "material": MaterialAgent(),
            "edit": EditAgent(),
            "animation": AnimationAgent(),
            "audio": AudioAgent(),
            "quality": QualityAgent(),
        }
        # Agent 级熔断: agent_name → {"fail_count": int, "last_fail_at": datetime}
        self._circuit_breakers: dict[str, dict] = {}
        # 熔断阈值: 连续失败 N 次后跳过该 agent
        self._circuit_breaker_threshold = 3
        # 熔断恢复时间: 失败后等待 N 秒再允许重试
        self._circuit_breaker_recovery_sec = 60

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
        # Layer 1: 持久化最终状态（在线程池执行）
        await loop.run_in_executor(None, self._persist_state, state, state.status.value, error_category)
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
        heal_count = 0

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
                input_data = self._build_input(agent_name, result_data, persona_config, plugin)
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
        quality_passed = False
        while not quality_passed and heal_count <= self.MAX_SELF_HEAL_LOOPS:
            tl = result_data.get("timeline")
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
                input_data = self._build_input(redo_agent, result_data, persona_config, plugin)
                redo_step = await self._run_agent(
                    state, redo_agent, input_data, agent_context, bus
                )
                if redo_step.result:
                    self._merge_agent_result(redo_agent, redo_step, result_data, bus, pid)

                # P1 FIX: 联动重做依赖于 redo_agent 的下游 agent
                downstream = self._get_downstream_agents(redo_agent)
                for dep_name in downstream:
                    if dep_name == "quality":
                        continue  # quality 由外层循环处理
                    add_event(pid, "system", "info",
                              f"自愈联动: 重做 {dep_name}（因 {redo_agent} 已重做）")
                    dep_input = self._build_input(dep_name, result_data, persona_config, plugin)
                    dep_step = await self._run_agent(
                        state, dep_name, dep_input, agent_context, bus
                    )
                    if dep_step and dep_step.result:
                        self._merge_agent_result(dep_name, dep_step, result_data, bus, pid)
            else:
                quality_passed = True

        # 清理注入的质检上下文
        agent_context.extra_params.pop("_quality_issues", None)
        agent_context.extra_params.pop("_quality_heal_count", None)

        # 完成
        state.shared_data["final_timeline"] = (
            bus.get_artifact("timeline").model_dump(mode="json")
            if bus.get_artifact("timeline")
            else None
        )

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

        # 非 timeline 字段正常合并
        for k, v in step.result.items():
            if k != "timeline":
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
        manifest = load_persona_by_id(request.persona_id)
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

    def _build_input(self, agent_name: str, result_data: dict, persona_config: dict, plugin) -> dict:
        """为 Agent 构建输入数据。"""
        inputs = {
            "structure": {
                "persona_config": persona_config,
                "persona_prompt": result_data.get("persona_prompt", ""),
                "rag_context": result_data.get("rag_context", ""),
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
            },
            "audio": {
                "timeline": result_data.get("timeline"),
                "audio_config": {
                    **persona_config.get("audio", {}),
                    "voice_id": persona_config.get("audio", {}).get("voice_clone_model_id")
                    or persona_config.get("audio", {}).get("voice")
                    or "",
                    "auto_dub": True,
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
            ), ctx)
        elif name == "edit":
            from clipwright.schema.agent import EditInput
            return await agent.execute(EditInput(
                context=ctx,
                script_skeleton=data.get("script_skeleton", {}),
                candidate_clips=data.get("candidate_clips", []),
            ), ctx)
        elif name == "animation":
            tl = data.get("timeline")
            from clipwright.schema.agent import AnimationInput
            return await agent.execute(AnimationInput(
                context=ctx,
                timeline=tl,
                visual_config=data.get("visual_config", {}),
            ), ctx)
        elif name == "audio":
            from clipwright.schema.agent import AudioInput
            return await agent.execute(AudioInput(
                context=ctx,
                timeline=data.get("timeline"),
                audio_config=data.get("audio_config", {}),
            ), ctx)
        elif name == "quality":
            from clipwright.schema.agent import QualityInput
            return await agent.execute(QualityInput(
                context=ctx,
                timeline=data.get("timeline"),
                constraints=data.get("constraints", {}),
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
            # 截断过大的 shared_data 字段，避免 MongoDB 16MB 限制
            truncated_shared = {}
            for k, v in (state.shared_data or {}).items():
                if isinstance(v, str) and len(v) > 5000:
                    truncated_shared[k] = v[:5000] + f"...[截断, 原长{len(v)}]"
                elif isinstance(v, (dict, list)):
                    s = str(v)
                    truncated_shared[k] = s[:5000] + f"...[截断]" if len(s) > 5000 else v
                else:
                    truncated_shared[k] = v
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
        - fatal: 系统级（内存不足、数据库断开）
        """
        transient_patterns = ["timeout", "超时", "rate limit", "too many", "暂时", "retry",
                              "connection", "reset", "timed out"]
        permanent_patterns = ["not found", "unknown", "invalid", "type", "格式错误",
                              "NoneType", "AttributeError", "KeyError"]
        for p in transient_patterns:
            if p in error.lower():
                return "transient"
        for p in permanent_patterns:
            if p in error.lower():
                return "permanent"
        return "permanent"

    @staticmethod
    def _should_stop(state: PipelineState, step: PipelineStep) -> bool:
        if step.status in (PipelineStatus.FAILED, PipelineStatus.CANCELLED):
            state.status = PipelineStatus.FAILED
            state.error = step.error or f"Agent {step.agent_name} failed"
            return True
        return False
