"""Pipeline 编排器 — 驱动 Agent 链路的顺序/条件执行。

核心职责：
1. 接收 PipelineRequest
2. 按顺序执行 Agent 节点（Structure → Material → Edit → Animation → Audio → Quality）
3. 在 Edit Agent 处注入 Persona 参数（经类型插件翻译）
4. 遇到 fail 时支持局部重执行
5. 输出最终时间线
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from clipwright.agents import (
    AnimationAgent,
    AudioAgent,
    EditAgent,
    MaterialAgent,
    QualityAgent,
    StructureAgent,
)
from clipwright.category import CategoryRegistry
from clipwright.persona.loader import (
    load_persona_by_id,
    resolve_inheritance,
)
from clipwright.persona.validator import validate_manifest
from clipwright.schema.agent import AgentContext, AgentDecision
from clipwright.schema.pipeline import (
    PipelineRequest,
    PipelineState,
    PipelineStatus,
    PipelineStep,
)
from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.trace import (
    add_event,
    create_trace,
    format_tool_call,
)


def get_agent_progress(agent_name: str) -> int:
    """C5: 返回某 agent 完成后的累计进度百分比（权重表累计）。未知 agent → 0。"""
    weights = PipelineOrchestrator.AGENT_PROGRESS_WEIGHTS
    if agent_name not in weights:
        return 0
    order = ["structure", "material", "edit", "animation", "audio", "quality"]
    total = 0
    for name in order:
        total += weights.get(name, 0)
        if name == agent_name:
            break
    return min(100, total)


class PipelineOrchestrator:
    """Pipeline 编排器，负责 Agent 链路的编排执行。"""

    # C5: Agent 阶段进度权重（总和 100），用于细粒度进度事件
    AGENT_PROGRESS_WEIGHTS = {
        "structure": 15,
        "material": 20,
        "edit": 30,
        "animation": 15,
        "audio": 10,
        "quality": 10,
    }

    def __init__(self) -> None:
        self._agents = {
            "structure": StructureAgent(),
            "material": MaterialAgent(),
            "edit": EditAgent(),
            "animation": AnimationAgent(),
            "audio": AudioAgent(),
            "quality": QualityAgent(),
        }

    async def run(self, request: PipelineRequest, pipeline_id: str = "") -> PipelineState:
        """执行完整的 Pipeline。"""
        pid = pipeline_id or f"pl_{uuid.uuid4().hex[:12]}"
        state = PipelineState(
            pipeline_id=pid,
            request=request,
        )
        if not pipeline_id:
            create_trace(pid)

        try:
            # 1. 加载 Persona
            manifest = load_persona_by_id(request.persona_id)
            manifest = resolve_inheritance(manifest)
            warnings = validate_manifest(manifest)
            if warnings:
                state.shared_data["persona_warnings"] = warnings
            add_event(state.pipeline_id, "system", "info",
                      f"加载 Persona: {request.persona_id}")

            # 2. 获取类型插件
            plugin = CategoryRegistry.get(request.category_plugin_id)
            if plugin is None:
                raise ValueError(f"Unknown category plugin: {request.category_plugin_id}")
            add_event(state.pipeline_id, "system", "plugin",
                      f"使用类型插件: {plugin.display_name} ({plugin.plugin_id})",
                      {"plugin_id": plugin.plugin_id, "display_name": plugin.display_name})

            # 3. 翻译 Persona 参数
            persona_config = manifest.parameter.model_dump(mode="json") if manifest.parameter else {}
            translated = plugin.translate_persona(manifest.parameter) if manifest.parameter else {}
            add_event(state.pipeline_id, "system", "plugin",
                      f"Persona 参数翻译: {list(translated.keys())}",
                      {"translated_params": translated})

            # 4. 加载 Prompt 和 RAG
            persona_prompt = manifest.prompt
            rag_context = ""
            if manifest.knowledge:
                try:
                    from clipwright.rag.retriever import Retriever
                    retriever = Retriever()
                    result = await retriever.retrieve(
                        request.persona_id,
                        request.topic,
                        top_k=3,
                        rerank=False,
                    )
                    if result.context:
                        rag_context = result.context
                except Exception as e:
                    logger.warning("RAG retrieval failed (non-fatal): %s", e)

            # 5. 构建 Agent 上下文（合并 Persona 翻译参数 + 前端传入参数）
            agent_context = AgentContext(
                pipeline_id=state.pipeline_id,
                persona_id=request.persona_id,
                category_plugin_id=request.category_plugin_id,
                topic=request.topic,
                extra_params={**translated, **request.extra_params},
            )
            logger.info("[时长诊断] AgentContext extra_params: %s",
                        json.dumps(agent_context.extra_params, ensure_ascii=False))

            # 6. 按序执行 Agent
            # Structure Agent
            step1 = await self._run_agent_step(
                state, "structure",
                {
                    "persona_config": persona_config,
                    "persona_prompt": persona_prompt,
                    "rag_context": rag_context,
                },
                agent_context,
            )
            if self._should_stop(state, step1):
                return state

            script_skeleton = step1.result.get("script_skeleton", {})
            scenes = step1.result.get("scenes", [])

            # Material Agent
            step2 = await self._run_agent_step(
                state, "material",
                {"script_skeleton": {"scenes": scenes, **script_skeleton},
                 "persona_config": persona_config},
                agent_context,
            )
            if self._should_stop(state, step2):
                return state

            candidate_clips = step2.result.get("candidate_clips", [])

            # Edit Agent
            step3 = await self._run_agent_step(
                state, "edit",
                {
                    "script_skeleton": {"scenes": scenes, **script_skeleton},
                    "candidate_clips": candidate_clips,
                },
                agent_context,
            )
            if self._should_stop(state, step3):
                return state

            timeline_data = step3.result.get("timeline")
            timeline = Timeline(**timeline_data) if timeline_data else None

            # P8: dry_run 预览模式 — 只生成粗剪时间线（structure→edit），
            # 不执行 animation/audio/quality（无需媒体与额外 LLM 成本），
            # 供前端「仅预览规划」场景快速出片。
            if request.dry_run:
                state.shared_data["dry_run"] = True
                state.shared_data["final_timeline"] = timeline_data
                add_event(state.pipeline_id, "system", "info",
                          "dry_run 模式：已生成粗剪时间线预览，跳过动画/音频/质检")
                state.status = PipelineStatus.COMPLETED
                return state

            # Animation Agent
            if timeline:
                visual_config = persona_config.get("visual", {})
                step4 = await self._run_agent_step(
                    state, "animation",
                    {"timeline": timeline, "visual_config": visual_config},
                    agent_context,
                )
                if self._should_stop(state, step4):
                    return state
                timeline_data = step4.result.get("timeline")
                timeline = Timeline(**timeline_data) if timeline_data else None

            # Audio Agent
            if timeline:
                audio_cfg = persona_config.get("audio", {})
                audio_config = {
                    **audio_cfg,
                    "voice_id": audio_cfg.get("voice_clone_model_id")
                    or audio_cfg.get("voice")
                    or agent_context.extra_params.get("voice_id", ""),
                    "auto_dub": agent_context.extra_params.get("auto_dub", True),
                }
                step5 = await self._run_agent_step(
                    state, "audio",
                    {"timeline": timeline, "audio_config": audio_config},
                    agent_context,
                )
                if self._should_stop(state, step5):
                    return state
                timeline_data = step5.result.get("timeline")
                timeline = Timeline(**timeline_data) if timeline_data else None

            # Quality Agent
            if timeline:
                constraints = persona_config.get("constraints", {})
                step6 = await self._run_agent_step(
                    state, "quality",
                    {"timeline": timeline, "constraints": constraints},
                    agent_context,
                )
                if self._should_stop(state, step6):
                    return state

            # 6. 完成
            state.status = PipelineStatus.COMPLETED
            state.shared_data["final_timeline"] = (
                timeline.model_dump(mode="json") if timeline else None
            )

        except Exception as e:
            state.status = PipelineStatus.FAILED
            state.error = str(e)
            logger.exception("Pipeline 执行异常: %s", e)

        state.updated_at = datetime.now()
        return state

    async def _run_agent_step(
        self,
        state: PipelineState,
        agent_name: str,
        input_data: dict,
        context: AgentContext,
    ) -> PipelineStep:
        """执行单个 Agent 步骤。"""
        step = state.add_step(agent_name)
        step.status = PipelineStatus.RUNNING
        step.started_at = datetime.now()

        agent = self._agents[agent_name]
        state.current_agent = agent_name
        pid = state.pipeline_id

        add_event(pid, agent_name, "agent_start", f"Agent 开始: {agent_name}")
        logger.debug("Pipeline Agent[%s] 输入: %s", agent_name,
                     json.dumps({k: str(v)[:200] for k, v in input_data.items()}, ensure_ascii=False))

        try:
            # 根据 agent_name 构造对应输入并执行
            result = await self._dispatch_agent(agent_name, input_data, context)
            step.result = result.model_dump(mode="json")
            logger.debug("Pipeline Agent[%s] 输出: decision=%s, error=%s, result_keys=%s",
                         agent_name, result.decision,
                         getattr(result, "error", None) or "none",
                         list(step.result.keys()) if step.result else "none")
            if result.decision != AgentDecision.FAIL:
                step.status = PipelineStatus.COMPLETED
            else:
                step.status = PipelineStatus.FAILED
                step.error = result.error or getattr(result, "error", None) or f"Agent {agent_name} returned FAIL"

            # 提取工具调用记录（从 Agent 的 tool_calls）
            tool_calls = step.result.get("tool_calls", []) or []
            for tc in (tool_calls if isinstance(tool_calls, list) else []):
                if isinstance(tc, dict):
                    t_name = tc.get("tool", "")
                    t_input = tc.get("input", {})
                    add_event(pid, agent_name, "tool",
                              f"调用工具: {format_tool_call(t_name, t_input)}",
                              tc)

            # 检查是否有 LLM 调用记录（从 Agent 的 llm_calls）
            llm_calls = step.result.get("llm_calls", []) or []
            for lc in (llm_calls if isinstance(llm_calls, list) else []):
                add_event(pid, agent_name, "llm",
                          f"LLM 调用: {(lc.get('model','') or lc.get('summary',''))[:100]}",
                          lc)

            add_event(pid, agent_name, "agent_end",
                      f"Agent 完成: {agent_name} → {step.status} ({step.duration_ms or '?'}ms)")

            # 保存到共享数据
            state.shared_data[f"{agent_name}_output"] = step.result

            # C4: 每个 Agent 完成后都写时间线快照到 trace（前端可逐阶段回放），
            # 优先取 agent 自己的 timeline 输出，否则回退共享的 final_timeline。
            timeline_snapshot = step.result.get("timeline") or state.shared_data.get("final_timeline")
            if timeline_snapshot:
                from clipwright.services.trace import add_event as te
                te(pid, agent_name, "timeline_snapshot",
                   f"时间线快照: {agent_name}",
                   timeline_snapshot)

        except Exception as e:
            step.status = PipelineStatus.FAILED
            step.error = str(e)
            add_event(pid, agent_name, "error", f"Agent 失败: {agent_name} → {str(e)[:200]}")
            logger.exception("Agent %s 执行异常: %s", agent_name, e)

        step.completed_at = datetime.now()
        if step.started_at:
            step.duration_ms = int(
                (step.completed_at - step.started_at).total_seconds() * 1000
            )
        state.updated_at = datetime.now()
        return step

    async def _dispatch_agent(self, name: str, data: dict, ctx: AgentContext) -> object:
        """分派 Agent 调用。"""
        agent = self._agents[name]
        if name == "structure":
            from clipwright.schema.agent import StructureInput
            return await agent.execute(
                StructureInput(
                    context=ctx,
                    persona_config=data.get("persona_config", {}),
                    persona_prompt=data.get("persona_prompt"),
                    rag_context=data.get("rag_context"),
                ), ctx,
            )
        elif name == "material":
            from clipwright.schema.agent import MaterialInput
            return await agent.execute(
                MaterialInput(context=ctx, script_skeleton=data.get("script_skeleton", {})), ctx,
            )
        elif name == "edit":
            from clipwright.schema.agent import EditInput
            return await agent.execute(
                EditInput(
                    context=ctx,
                    script_skeleton=data.get("script_skeleton", {}),
                    candidate_clips=data.get("candidate_clips", []),
                ), ctx,
            )
        elif name == "animation":
            from clipwright.schema.agent import AnimationInput
            return await agent.execute(
                AnimationInput(
                    context=ctx,
                    timeline=data.get("timeline"),
                    visual_config=data.get("visual_config", {}),
                ), ctx,
            )
        elif name == "audio":
            from clipwright.schema.agent import AudioInput
            return await agent.execute(
                AudioInput(context=ctx, timeline=data.get("timeline"), audio_config=data.get("audio_config", {})), ctx,
            )
        elif name == "quality":
            from clipwright.schema.agent import QualityInput
            return await agent.execute(
                QualityInput(
                    context=ctx,
                    timeline=data.get("timeline"),
                    constraints=data.get("constraints", {}),
                ), ctx,
            )
        raise ValueError(f"Unknown agent: {name}")

    @staticmethod
    def _should_stop(state: PipelineState, step: PipelineStep) -> bool:
        if step.status in (PipelineStatus.FAILED, PipelineStatus.CANCELLED):
            state.status = PipelineStatus.FAILED
            state.error = step.error or f"Agent {step.agent_name} failed (unknown error)"
            return True
        return False
