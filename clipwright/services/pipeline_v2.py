"""Pipeline 编排器 v2 — 动态 Agent 路由 + 自愈循环。"""

from __future__ import annotations

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
from clipwright.services.trace import add_event, create_trace, get_all_events, format_tool_call
from clipwright.tool.registry import ToolRegistry


class AgentDAG:
    """Agent 依赖关系图 — 用于并行调度。

    定义哪些 Agent 可以并行执行（无依赖关系）。
    """

    # Agent 依赖关系: {agent: [依赖的agent列表]}
    # None 依赖 → 可以立即执行
    # 空列表 → 无依赖，但不是入口点
    _DEPS: dict[str, list[str]] = {
        "structure": [],        # 入口点，无依赖
        "material": ["edit"],   # Material 的"最终结果"依赖 edit（但预搜索可提前）
        "edit": ["structure", "material"],
        "animation": ["edit"],
        "audio": ["edit"],      # Audio 和 Animation 无依赖关系，可并行
        "quality": ["animation", "audio"],
    }

    # 真正可以在"准备阶段"提前运行的无依赖 Agent 分组
    _PARALLEL_GROUPS: list[list[str]] = [
        ["structure"],                          # 阶段 1: 结构分析
        ["material_prefetch"],                  # 阶段 2: 素材预搜索
        ["edit"],                               # 阶段 3: 编辑
        ["animation", "audio"],                 # 阶段 4: 动画 + 音频并行（依赖 edit）
        ["quality"],                            # 阶段 5: 质检
    ]

    @classmethod
    def get_execution_plan(cls) -> list[list[str]]:
        """返回分阶段的并行执行计划。"""
        return cls._PARALLEL_GROUPS


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

    async def run(
        self,
        request: PipelineRequest,
        pipeline_id: str = "",
    ) -> PipelineState:
        pid = pipeline_id or f"pl_v2_{uuid.uuid4().hex[:12]}"
        state = PipelineState(pipeline_id=pid, request=request)
        bus = AgentBus(pid)

        if not pipeline_id:
            create_trace(pid)

        try:
            # 初始化
            manifest, plugin, persona_config, translated, agent_context = await self._init(
                request, pid, state, bus
            )

            # 并行 Agent 执行（按 DAG 分组）
            heal_count = 0
            result_data: dict[str, Any] = {}

            for group_idx, group in enumerate(AgentDAG.get_execution_plan()):
                logger.info("PipelineV2 执行组 [%d]: %s", group_idx, group)
                add_event(pid, "system", "info", f"执行组[{group_idx}]: {'+'.join(group)}")

                # 并行执行组内所有无依赖的 Agent
                tasks = {}
                for agent_name in group:
                    if agent_name == "material_prefetch":
                        # 素材预搜索（与 audio 并行）
                        input_data = self._build_input("material", result_data, persona_config, plugin)
                        tasks[agent_name] = self._run_agent(
                            state, "material", input_data, agent_context, bus
                        )
                    else:
                        input_data = self._build_input(agent_name, result_data, persona_config, plugin)
                        tasks[agent_name] = self._run_agent(
                            state, agent_name, input_data, agent_context, bus
                        )

                # 等待所有并行 Agent 完成
                import asyncio
                completed = await asyncio.gather(*tasks.values(), return_exceptions=True)

                for (name, _), result in zip(tasks.items(), completed):
                    if isinstance(result, Exception):
                        logger.error("并行 Agent %s 异常: %s", name, result)
                        add_event(pid, name, "error", f"{name} 异常: {str(result)[:200]}")
                        state.status = PipelineStatus.FAILED
                        state.error = str(result)
                        break

                    if hasattr(result, "result") and result.result:
                        result_data.update(result.result)
                        # 调试日志
                        _dbg_tl = result.result.get("timeline")
                        _dbg_tr = (len(_dbg_tl.get("tracks", [])) if isinstance(_dbg_tl, dict)
                                   else (len(_dbg_tl.tracks) if _dbg_tl and hasattr(_dbg_tl, "tracks") else -1))
                        logger.info("PipelineV2 result_data 更新: agent=%s, timeline=%s, tracks=%d",
                                    name, "有" if _dbg_tl else "无", _dbg_tr)

                    # 提取关键数据
                    if name == "structure" or (name == "edit" and result.result):
                        scenes = result.result.get("scenes", [])
                        bus.set_artifact("scenes", scenes)
                    if name == "edit" and result.result:
                        tl_data = result.result.get("timeline")
                        if tl_data:
                            try:
                                timeline = Timeline(**tl_data) if isinstance(tl_data, dict) else tl_data
                                bus.set_artifact("timeline", timeline)
                                result_data["timeline"] = timeline
                            except Exception as e:
                                logger.warning("Timeline 解析失败: %s", e)
                    # 动画 Agent 运行后同步更新总线时间线（含动画轨和文字轨 keyframes）
                    if name == "animation" and result.result:
                        tl_data = result.result.get("timeline")
                        logger.info("AnimationAgent 结果: timeline=%s, tracks=%d",
                                    "存在" if tl_data else "不存在",
                                    len(tl_data.get("tracks", [])) if tl_data else 0)
                        if tl_data:
                            try:
                                timeline = Timeline(**tl_data) if isinstance(tl_data, dict) else tl_data
                                bus.set_artifact("timeline", timeline)
                                result_data["timeline"] = timeline
                                logger.info("AnimationAgent 时间线已同步到总线: %d 个轨道",
                                            len(timeline.tracks))
                            except Exception as e:
                                logger.warning("Animation timeline 解析失败: %s", e)

                if state.status == PipelineStatus.FAILED:
                    break

            # 质检 + 自愈循环
            quality_passed = False
            while not quality_passed and heal_count <= self.MAX_SELF_HEAL_LOOPS:
                tl = result_data.get("timeline")
                step = await self._run_agent(
                    state, "quality",
                    {"timeline": tl, "constraints": persona_config.get("constraints", {})},
                    agent_context, bus,
                )
                if step.result:
                    result_data.update(step.result)

                # 检查是否需要自愈
                redo_agent = ""
                if step.result:
                    redo_agent = step.result.get("redo_agent", "")
                    issues = step.result.get("issues", [])
                    has_errors = any(i.get("severity") == "error" for i in issues)
                else:
                    has_errors = False

                if has_errors and redo_agent and heal_count < self.MAX_SELF_HEAL_LOOPS:
                    heal_count += 1
                    logger.info("自愈循环 [%d/%d]: → 重做 %s",
                                heal_count, self.MAX_SELF_HEAL_LOOPS, redo_agent)
                    add_event(pid, "system", "info", f"自愈[{heal_count}]: 重做 {redo_agent}")
                    # 重做指定的 Agent
                    input_data = self._build_input(redo_agent, result_data, persona_config, plugin)
                    redo_step = await self._run_agent(
                        state, redo_agent, input_data, agent_context, bus
                    )
                    if redo_step.result:
                        result_data.update(redo_step.result)
                        if redo_agent == "edit":
                            tl_data = redo_step.result.get("timeline")
                            if tl_data:
                                try:
                                    timeline = Timeline(**tl_data) if isinstance(tl_data, dict) else tl_data
                                    bus.set_artifact("timeline", timeline)
                                    result_data["timeline"] = timeline
                                except Exception:
                                    pass
                        if redo_agent in ("edit", "animation"):
                            anim_input = self._build_input("animation", result_data, persona_config, plugin)
                            anim_step = await self._run_agent(
                                state, "animation", anim_input, agent_context, bus
                            )
                            if anim_step and anim_step.result:
                                result_data.update(anim_step.result)
                                anim_tl = anim_step.result.get("timeline")
                                if anim_tl:
                                    try:
                                        anim_timeline = Timeline(**anim_tl) if isinstance(anim_tl, dict) else anim_tl
                                        bus.set_artifact("timeline", anim_timeline)
                                        result_data["timeline"] = anim_timeline
                                    except Exception:
                                        pass
                else:
                    quality_passed = True

            # 完成
            state.shared_data["final_timeline"] = (
                bus.get_artifact("timeline").model_dump(mode="json")
                if bus.get_artifact("timeline")
                else None
            )

            if state.status != PipelineStatus.FAILED:
                state.status = PipelineStatus.COMPLETED

        except Exception as e:
            state.status = PipelineStatus.FAILED
            state.error = str(e)
            logger.exception("PipelineV2 执行异常: %s", e)

        state.updated_at = datetime.now()
        return state

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

        # Agent 上下文（合并前端参数）
        agent_context = AgentContext(
            pipeline_id=pid,
            persona_id=request.persona_id,
            category_plugin_id=request.category_plugin_id,
            topic=request.topic,
            extra_params={**translated, **request.extra_params},
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
            },
            "edit": {
                "script_skeleton": result_data,
                "candidate_clips": result_data.get("candidate_clips", []),
            },
            "animation": {
                "timeline": result_data.get("timeline"),
            },
            "audio": {
                "timeline": result_data.get("timeline"),
            },
            "quality": {
                "timeline": result_data.get("timeline"),
                "constraints": persona_config.get("constraints", {}),
            },
        }
        return inputs.get(agent_name, {})

    async def _run_agent(self, state, agent_name, input_data, context, bus):
        """执行单个 Agent，集成总线通信。"""
        step = state.add_step(agent_name)
        step.status = PipelineStatus.RUNNING
        step.started_at = datetime.now()
        state.current_agent = agent_name
        pid = state.pipeline_id

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
            else:
                step.status = PipelineStatus.FAILED
                step.error = getattr(result, "error", None) or f"{agent_name} failed"

            # 发布到总线
            bus.publish(agent_name, "result", {
                "decision": str(result.decision),
                "keys": list(step.result.keys()) if step.result else [],
            })

            # 如果有 scenes，发布场景信息
            scenes = step.result.get("scenes", [])
            if scenes:
                bus.publish(agent_name, "scenes", {
                    "count": len(scenes),
                    "total_duration": sum(s.get("duration_sec", 0) for s in scenes),
                })

            add_event(pid, agent_name, "agent_end",
                      f"{agent_name} → {step.status} ({step.duration_ms or '?'}ms)")

            if agent_name == "edit":
                # 编辑 Agent 完成后发布镜头需求到总线
                timeline_data = step.result.get("timeline")
                if timeline_data and isinstance(timeline_data, dict):
                    clips_info = []
                    for t in timeline_data.get("tracks", []):
                        for c in (t.get("clips", []) or []):
                            if c is None:
                                continue
                            clips_info.append({
                                "kind": c.get("kind", "") if c else "",
                                "start": c.get("start_sec", 0) if c else 0,
                                "duration": c.get("duration_sec", 0) if c else 0,
                                "text": (c.get("text", "") or "")[:30],
                            })
                    bus.publish("edit", "clips", clips_info)

        except Exception as e:
            step.status = PipelineStatus.FAILED
            step.error = str(e)
            add_event(pid, agent_name, "error", f"{agent_name} 异常: {str(e)[:200]}")
            logger.exception("Agent %s 异常: %s", agent_name, e)

        return step

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
            ), ctx)
        elif name == "material":
            from clipwright.schema.agent import MaterialInput
            return await agent.execute(MaterialInput(
                context=ctx,
                script_skeleton=data.get("script_skeleton", {}),
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
            tl_tracks = len(tl.tracks) if tl and hasattr(tl, "tracks") else (len(tl.get("tracks", [])) if tl and isinstance(tl, dict) else -1)
            logger.info("AnimationAgent 输入: timeline=%s, tracks=%d",
                        "存在" if tl else "空", tl_tracks)
            from clipwright.schema.agent import AnimationInput
            return await agent.execute(AnimationInput(
                context=ctx,
                timeline=tl,
            ), ctx)
        elif name == "audio":
            from clipwright.schema.agent import AudioInput
            return await agent.execute(AudioInput(
                context=ctx,
                timeline=data.get("timeline"),
            ), ctx)
        elif name == "quality":
            from clipwright.schema.agent import QualityInput
            return await agent.execute(QualityInput(
                context=ctx,
                timeline=data.get("timeline"),
                constraints=data.get("constraints", {}),
            ), ctx)
        raise ValueError(f"Unknown agent: {name}")

    @staticmethod
    def _should_stop(state: PipelineState, step: PipelineStep) -> bool:
        if step.status in (PipelineStatus.FAILED, PipelineStatus.CANCELLED):
            state.status = PipelineStatus.FAILED
            state.error = step.error or f"Agent {step.agent_name} failed"
            return True
        return False
