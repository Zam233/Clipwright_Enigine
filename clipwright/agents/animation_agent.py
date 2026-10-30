"""动画 Agent（AnimationAgent）— 将动画标记转为时间线修改。

两种动画类型：
1. 文字动画 [文字动画]xxx — 作用于文字轨 clip 的入场 keyframes + 出场淡出
2. 逻辑动画 [逻辑动画]xxx — 独立动画轨，展示逻辑关系（箭头/对比/流程/因果）

动画类型从 AnimationCatalog 动态读取，支持插件扩展。
"""

from __future__ import annotations

import asyncio
import hashlib
import httpx
import json
import re
from pathlib import Path
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.agents.base import uid as _uid
from clipwright.animation.catalog import AnimationCatalog
from clipwright.animation.registry import AnimationRegistry
from clipwright.config import logger, settings
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AnimationInput,
    AnimationOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.services.subtitle import DEFAULT_CAPTION_STYLE, DEFAULT_TEXT_STYLE
from clipwright.services.degradation import note_degradation as _note
from clipwright.services.agent_context import emit_progress as _emit_progress
from clipwright.services.trace import add_event
from clipwright.services.web_search import WebSearchService

# 数据/事实型 mg_dynamic 检测 — 命中则触发联网搜索，注入事实上下文
# （中文 + 英文，大小写不敏感；图表/数据/百分比/年份/引语/增长率/GDP/报告等）
_DATA_FACT_RE = re.compile(
    r"图表|数据|统计|对比|百分比|%|年份 20\d\d|引语|quote|citation|增长率|GDP|报告|survey",
    re.IGNORECASE,
)


def _merge_style_defaults(
    base: dict[str, Any], persona_style: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 persona_style 提供的样式覆盖到统一默认样式上（仅覆盖已定义样式字段）。

    任务 32：生成 clip 必须携带完整样式字段；persona_style 未提供时使用统一默认值。
    """
    style = dict(base)
    for k in style:
        v = (persona_style or {}).get(k)
        if v is not None:
            style[k] = v
    return style


class AnimationAgent(BaseAgent[AnimationInput, AnimationOutput]):
    """动画 Agent：解析 [文字动画] / [逻辑动画] / [过渡动画] 标记 → 修改时间线。

    文字动画：在文字轨创建/更新 text clip，添加完整 keyframes（入场+保持+出场）。
    逻辑动画：在动画轨创建独立 clip，将 diagram 参数存入 metadata 供 Render 使用。
    过渡动画：设置 clip 的 transition_in 字段，供 RenderService 使用 xfade filter。
    """

    agent_name = "animation_agent"

    #: 能力 → 优先工具（插件可用 capability 声明接管；白名单随注册表实时变化）
    capability_prefer: dict[str, tuple[str, ...]] = {}
    tool_whitelist: tuple[str, ...] = (
        "list_animations", "describe_llm_mg",
        "pv_engine_doctor", "pv_engine_stills",
    )

    def _text_anim_defs(self) -> list[Any]:
        """当前已注册的文字动画（实时；插件增删立即生效）。"""
        try:
            from clipwright.animation.registry import AnimationRegistry, AnimationType

            return list(AnimationRegistry.list(AnimationType.TEXT))
        except Exception:  # noqa: BLE001 — 注册表不可用时退回 catalog
            return []

    def _anim_properties(self, anim_id: str) -> set[str]:
        """该动画全部关键帧里出现过的属性名（判断"是不是逐字/淡入"的语义依据）。"""
        for anim in self._text_anim_defs():
            if str(getattr(anim, "animation_id", "")) != anim_id:
                continue
            props: set[str] = set()
            for kf in getattr(anim, "keyframes", []) or []:
                props |= set(getattr(kf, "properties", {}) or {})
            return props
        return set()

    def _default_text_anim_id(self) -> str:
        """默认文字动画：取"有 opacity 曲线"的第一个，再退第一个，最后才用历史默认值。"""
        defs = self._text_anim_defs()
        for anim in defs:
            if "opacity" in self._anim_properties(str(getattr(anim, "animation_id", ""))):
                return str(anim.animation_id)
        if defs:
            return str(getattr(defs[0], "animation_id", "text_fade_in"))
        return "text_fade_in"

    async def _ingest_web_media(self, context: Any, *, topic: str) -> int:
        """用 web 搜索把**真实图片/视频**落进 `materials/web/` 并入图片语义索引。

        PRD web-search-fetch M2：旧实现只把 3 条摘要塞进 prompt，搜索结果里的
        图片/视频从不落盘，"动画自己找素材"不成立。这里：
        1. 只在 web 搜索可用（有 provider）且有工作区时执行；
        2. 取前 `limit` 条**媒体**结果，经 `web_assets` 做 SSRF 校验 + sha256 去重
           + 旁车 meta（来源/许可/抓取时间）；
        3. 成功的条目并入 `self._image_index`，MG 生成阶段可以按语义选图引用。
        """
        try:
            from clipwright.config import settings
            if not bool(getattr(settings, "web_assets_ingest", True)):
                return 0
        except Exception:  # noqa: BLE001
            pass
        extra = dict(getattr(context, "extra_params", None) or {})
        root = str(extra.get("workspace_root") or "")
        if not root or not topic.strip():
            return 0
        try:
            # 走工具面（能力解析 → web_search）：每次检索都进 `last_tool_calls` 与
            # `subagent_tool` 事件，主 agent 才看得见"动画阶段自己联网找了图"。
            found = await self.call_tool("web_search", query=topic[:120], max_results=6)
            payload = getattr(found, "output", None) or {}
            results = list(payload.get("results") or []) if isinstance(payload, dict) else []
            if not results and "not_found" in str(getattr(found, "status", "")).lower():
                # 工具面没注册（脚本/单测直调 Agent）：回落到服务本体，行为与旧实现一致
                from clipwright.services.web_search import WebSearchService

                svc = WebSearchService()
                if svc.is_configured():
                    results = await svc.search(topic[:120], max_results=6)
            if not results:
                return 0
            media: list[dict[str, Any]] = []
            for item in results:
                url = str(item.get("url") or "")
                lower = url.lower().split("?")[0]
                if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm")):
                    media.append(item)
            if not media:
                return 0
            from clipwright.services import web_assets
            out = await web_assets.ingest_results(media, root=root, limit=2)
            added = 0
            for res in out:
                if not (res.ok and res.kind in ("images", "videos")):
                    continue
                self._image_index.append({
                    "path": res.local_path,
                    "tags": [topic[:60]],
                    "description": res.title or "联网素材",
                    "source": res.url,
                    "license": res.license,
                })
                added += 1
            if added:
                logger.info("AnimationAgent: 联网素材入库 %d 项（materials/web/）", added)
            return added
        except Exception as exc:  # noqa: BLE001 — 联网素材化失败不影响动画产出
            logger.warning("AnimationAgent: _ingest_web_media 失败: %s", exc)
            return 0

    def _is_typewriter_like(self, anim_id: str) -> bool:
        """是否逐字出现：按关键帧属性 ``char_progress`` 判定，不再写死 id 白名单。"""
        return "char_progress" in self._anim_properties(anim_id)

    def __init__(self) -> None:
        super().__init__()
        self._catalog = AnimationCatalog()
        self._mg_category_context: dict = {}  # 视频类型（category）特征数据
        # 时间线分辨率/帧率（execute() 中从 Timeline 读取，供 MG 渲染器动态解析）
        self._tl_width = 1920
        self._tl_height = 1080
        self._tl_fps = 30.0
        # 图片素材库语义索引 {path, tags, description} — execute() 中构建，
        # 供 mg_dynamic 生成 prompt 让 LLM 主动选择语义匹配图片（防 📡→石墨烯 类错配）
        self._image_index: list[dict[str, Any]] = []

    async def execute(
        self, input_data: AnimationInput, context: AgentContext
    ) -> AnimationOutput:
        logger.info("AnimationAgent 开始 pipeline=%s", context.pipeline_id[:12])
        self._pid = context.pipeline_id
        add_event(context.pipeline_id, "animation", "agent_start",
                  "AnimationAgent 开始（文字动画/逻辑动画分流）")
        # 工具/技能列表实时可见（插件增删立即生效）——写进 trace 便于排障
        _live_tools = [s["function"]["name"] for s in self.tool_schemas()]
        _live_skills = [s.name for s in self.list_skills()]
        add_event(context.pipeline_id, "animation", "capabilities",
                  f"实时工具: {', '.join(_live_tools) or '无'}｜实时技能: {', '.join(_live_skills) or '无'}")

        # 捕获 Persona 的 prompt / 视觉需求 prompt，供 MG 处理器与风格解析使用
        self._persona_prompt = getattr(input_data, "persona_prompt", None) or ""
        self._vision_prompt = getattr(input_data, "vision_prompt", None) or ""

        # PRD §16 R1/R3 双保险：即使 stage_inputs 没装配（老路径 / 直接调用 execute），
        # Agent 自己也按 persona_id 走一遍 board→extra→persona_store 兜底，
        # 并把 style_hint / fingerprint 带进 persona_style（R6 供模板与插件路径使用）。
        try:
            from clipwright.services.visual_context import load as _load_visual_context

            self._visual_ctx = _load_visual_context(
                str(getattr(context, "persona_id", "") or ""),
                board={
                    "persona_prompt": getattr(input_data, "persona_prompt", None),
                    "visual_config": getattr(input_data, "visual_config", None),
                    "vision_prompt": getattr(input_data, "vision_prompt", None),
                },
                extra=getattr(context, "extra_params", {}) or {},
            )
            if not self._persona_prompt:
                self._persona_prompt = self._visual_ctx.persona_prompt
            if not self._vision_prompt:
                self._vision_prompt = self._visual_ctx.vision_prompt
            add_event(
                context.pipeline_id, "animation", "visual_context",
                f"视觉上下文 source={self._visual_ctx.source} "
                f"prompt={len(self._visual_ctx.persona_prompt)}字 "
                f"palette={bool(self._visual_ctx.visual_config.get('palette'))}",
                self._visual_ctx.to_trace(),
            )
        except Exception:  # noqa: BLE001 — 装配失败不阻断动画阶段
            logger.warning("AnimationAgent: 视觉上下文装配失败", exc_info=True)
            self._visual_ctx = None

        try:
            timeline = input_data.timeline
            if timeline is None or not timeline.tracks:
                logger.info("AnimationAgent: 时间线为空，跳过")
                return AnimationOutput(decision=AgentDecision.PASS, timeline=timeline)

            # 读取时间线拟定分辨率/帧率（0/None 回退 1920x1080/30），
            # 供 MG 渲染器动态解析 — 不再让 Hyperframes 默认 1080x1920 竖屏
            self._tl_width = getattr(timeline, "width", 0) or 1920
            self._tl_height = getattr(timeline, "height", 0) or 1080
            self._tl_fps = getattr(timeline, "fps", 0) or 30.0

            # 解析视觉风格（LLM 驱动，支持插件覆盖）
            persona_style = await self._resolve_style(
                input_data.visual_config, context.extra_params,
            )
            # R6：把 persona 摘要/指纹/来源挂到样式上——模板与插件路径靠它拿到风格，
            # 生成缓存也用它区分不同 persona（避免跨人格复用动画）。
            if isinstance(self._visual_ctx, object) and self._visual_ctx is not None:
                persona_style = dict(persona_style or {})
                persona_style.setdefault("style_hint", self._visual_ctx.animation_style_hint())
                persona_style.setdefault("persona_fingerprint", self._visual_ctx.fingerprint)
                persona_style.setdefault("persona_source", self._visual_ctx.source)
                if self._visual_ctx.visual_config.get("palette"):
                    persona_style.setdefault("palette", self._visual_ctx.visual_config["palette"])

            # /D5: 图片语义索引 — 显式 image_assets（AI 生成图）∪ 素材库主动检索图片。
            # 对合并结果逐个调用视觉分析器（VisionService.analyze_image）构建
            # {path, tags, description}；分析失败回退文件名标签；两者皆空时保持
            # 原有行为（不调用分析器）。
            self._image_index = []
            try:
                provided = list(getattr(input_data, "image_assets", None) or [])
                found = await self._search_library_images(
                    topic=str(context.topic or ""),
                    creative_brief=getattr(input_data, "creative_brief", None),
                )
                merged: dict[str, dict[str, Any]] = {}
                for entry in [*provided, *found]:
                    path = str(entry.get("path") or entry.get("src") or "")
                    if path and path not in merged:
                        merged[path] = entry
                if merged:
                    self._image_index = await self._build_image_semantic_index(list(merged.values()))
                    if self._image_index:
                        logger.info(
                            "AnimationAgent: 图片语义索引 %d 项（显式 %d + 素材库检索 %d）",
                            len(self._image_index), len(provided), len(found),
                        )
                        add_event(context.pipeline_id, "animation", "image_index",
                                  f"图片语义索引 {len(self._image_index)} 项（LLM 选图入动画）")

                # 联网素材化（PRD web-search-fetch M2）：素材库没有可用图时，
                # 用 web 搜索找到**真实**图片/视频落进 materials/web/ 并并入语义索引，
                # 让 MG 生成可以引用它们（旧行为：搜索结果只当文字事实参考）。
                try:
                    added = await self._ingest_web_media(
                        context, topic=str(context.topic or ""),
                    )
                    if added:
                        add_event(context.pipeline_id, "animation", "web_assets",
                                  f"联网素材入库 {added} 项（已并入图片语义索引）")
                except Exception as exc:  # noqa: BLE001 — 联网素材化失败不影响动画
                    logger.warning("AnimationAgent: 联网素材入库失败（跳过）: %s", exc)
            except Exception:
                logger.exception("AnimationAgent: 图片语义索引构建失败，忽略图片素材库")
                self._image_index = []

            # 解析视频类型（category）特征，注入 LLM MG 生成器
            # （由引擎结合 Persona + 类型数据自行决定动画设计）
            self._mg_category_context = {}
            try:
                if context.category_plugin_id:
                    from clipwright.category.registry import CategoryRegistry
                    cat = CategoryRegistry.get(context.category_plugin_id)
                    if cat is not None:
                        self._mg_category_context = {
                            "plugin_id": getattr(cat, "plugin_id", ""),
                            "display_name": getattr(cat, "display_name", ""),
                            "description": getattr(cat, "description", ""),
                        }
                        if hasattr(cat, "get_shot_params"):
                            try:
                                self._mg_category_context["shot_params"] = cat.get_shot_params({})
                            except Exception:
                                pass
                        if hasattr(cat, "get_pacing"):
                            try:
                                self._mg_category_context["pacing"] = cat.get_pacing()
                            except Exception:
                                pass
                        if hasattr(cat, "get_mg_style_guidance"):
                            try:
                                guidance = cat.get_mg_style_guidance()
                                self._mg_category_context["mg_style_guidance"] = guidance
                            except Exception:
                                pass
                        # V1：动画阶段的类型规范（10 槽位里的 animation）进 MG 提示词
                        try:
                            from clipwright.services.type_spec_runtime import stage_text

                            anim_stage = stage_text(
                                getattr(context, "category_plugin_id", "") or "", "animation"
                            )
                            if anim_stage:
                                self._mg_category_context["stage_prompt"] = anim_stage
                        except Exception:
                            pass
            except Exception:
                self._mg_category_context = {}

            # 简报动画风格（style/tone/fonts/icons）并入 MG 生成上下文
            try:
                brief = input_data.creative_brief or {}
                brief_anim_style = brief.get("animation_style") or {}
                if isinstance(brief_anim_style, dict) and brief_anim_style:
                    self._mg_category_context["brief_animation_style"] = brief_anim_style
                if brief.get("asset_ratio"):
                    self._mg_category_context["brief_asset_ratio"] = brief.get("asset_ratio")
            except Exception:
                pass

            text_track = self._find_or_create_track(timeline, ClipKind.TEXT, "文字轨", 1)
            anim_track = None  # 延迟创建

            text_anim_count = 0
            logic_anim_count = 0
            transition_anim_count = 0
            self._llm_mg_generated = 0
            prev_clip = None
            # 开场不生成动画标记由 StructureAgent 源头保证（_strip_opening_animation_markers），
            # AnimationAgent 不做位置判断跳过——此处只按 description 标记忠实执行。
            # 收集逻辑动画（含 LLM MG）作业，主循环扫描后再并发执行——
            # 每个 LLM MG 生成 2-4 分钟，串行是动画阶段的最大瓶颈
            logic_job_t = tuple[Track, Clip, str, str, dict[str, Any], dict[str, Any] | None]
            logic_jobs: list[logic_job_t] = []

            # 遍历所有 video/image 轨 clip，检测标记
            for vid_track in timeline.tracks:
                if str(vid_track.kind) not in ("video", "image"):
                    continue
                prev_clip = None  # 每轨道重置，防止跨轨道转场

                for clip in list(vid_track.clips or []):
                    meta = clip.metadata or {}
                    desc = meta.get("description", "") or ""

                    markers = AnimationCatalog.parse_marker_from_description(desc)
                    if not markers:
                        prev_clip = clip
                        continue

                    # 遍历全部标记（原实现只取 markers[0]，多标记场景
                    # 其余动画意图静默丢失）
                    for marker in markers:
                        marker_type = marker.get("type", "text")
                        anim_id = marker.get("anim_id") or self._default_text_anim_id()
                        anim_name = marker.get("name", "淡入")

                        if marker_type == "text":
                            await self._handle_text_animation_async(
                                text_track, clip, anim_id, anim_name,
                                marker, persona_style,
                            )
                            text_anim_count += 1
                            logger.info("AnimationAgent: [文字动画]%s → %s (id=%s)",
                                        anim_name, clip.id[:8], anim_id)
                            add_event(context.pipeline_id, "animation", "text_anim",
                                      f"[文字动画]{anim_name}({anim_id}) → clip={clip.id[:8]}")

                        elif marker_type == "logic":
                            anim_track = anim_track or self._ensure_anim_track(timeline)
                            logic_jobs.append(
                                (anim_track, clip, anim_id, anim_name, marker, persona_style)
                            )
                            logic_anim_count += 1
                            logger.info("AnimationAgent: [逻辑动画]%s → %s (id=%s)",
                                        anim_name, clip.id[:8], anim_id)
                            add_event(context.pipeline_id, "animation", "logic_anim",
                                      f"[逻辑动画]{anim_name}({anim_id}) → clip={clip.id[:8]}")

                        elif marker_type == "transition":
                            self._handle_transition_animation(
                                clip, prev_clip, anim_id, anim_name,
                            )
                            transition_anim_count += 1
                            logger.info("AnimationAgent: [过渡动画]%s → %s (id=%s)",
                                        anim_name, clip.id[:8], anim_id)
                            add_event(context.pipeline_id, "animation", "transition_anim",
                                      f"[过渡动画]{anim_name}({anim_id}) → clip={clip.id[:8]}")

                    prev_clip = clip

            # 并发执行逻辑动画作业（有界并发；clips 写入后按 start_sec 排序保证 z-order）
            # 类型化 LLM 生成是等待型：优先用 animation_llm_concurrency，
            # 未配置则回落到 pipeline_concurrency（保持旧行为）。
            if logic_jobs:
                _conc = (getattr(settings, "animation_llm_concurrency", None)
                         or getattr(settings, "pipeline_concurrency", 3))
                sem = asyncio.Semaphore(max(1, int(_conc)))
                _total_jobs = len(logic_jobs)
                _done_jobs = {"n": 0}

                async def _run_logic(job):
                    async with sem:
                        track, c, aid, aname, m, style = job
                        # 段落进度进**任务事件流**（主 agent 能看见 29 段逐段推进；
                        # pipeline trace 是另一条通道，前端任务详情读的是 task events）
                        _emit_progress(
                            f"动画 {_done_jobs['n'] + 1}/{_total_jobs} 开始：{aname}",
                            {"anim_id": aid, "clip_id": c.id, "total": _total_jobs},
                        )
                        add_event(
                            context.pipeline_id, "animation", "mg_start",
                            f"动画生成开始: {aname} → {c.id[:8]}",
                            {"anim_id": aid, "clip_id": c.id},
                        )
                        try:
                            await self._handle_logic_animation(
                                track, c, aid, aname, m, style,
                            )
                            _done_jobs["n"] += 1
                            _emit_progress(
                                f"动画 {_done_jobs['n']}/{_total_jobs} 完成：{aname}",
                                {"anim_id": aid, "clip_id": c.id, "total": _total_jobs,
                                 "done": _done_jobs["n"]},
                            )
                            add_event(
                                context.pipeline_id, "animation", "mg_end",
                                f"动画生成完成: {aname} → {c.id[:8]}",
                                {"anim_id": aid, "clip_id": c.id},
                            )
                        except Exception as e:
                            logger.exception("AnimationAgent: 逻辑动画 %s 异常: %s", aname, e)
                            _emit_progress(
                                f"动画失败：{aname}（{type(e).__name__}）",
                                {"anim_id": aid, "clip_id": c.id, "error": str(e)[:200]},
                            )
                            add_event(
                                context.pipeline_id, "animation", "mg_end",
                                f"动画生成失败: {aname} → {c.id[:8]}",
                                {"anim_id": aid, "clip_id": c.id, "error": str(e)[:200]},
                            )

                await asyncio.gather(*(_run_logic(j) for j in logic_jobs))

            summary = (
                f"AnimationAgent: 文字动画={text_anim_count}, 逻辑动画={logic_anim_count}, "
                f"过渡动画={transition_anim_count}"
            )
            logger.info(summary)
            add_event(context.pipeline_id, "animation", "agent_end", summary)

            return AnimationOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                animation_plan={
                    "text_animations": text_anim_count,
                    "logic_animations": logic_anim_count,
                    "persona_style": persona_style,
                },
                generated_mg_count=self._llm_mg_generated,
            )

        except Exception as e:
            logger.exception("AnimationAgent 异常: %s", e)
            return self.build_error_output(str(e), AnimationOutput)

    # ── 文字动画处理 ──────────────────────────────────────

    async def _handle_text_animation_async(
        self,
        text_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        marker: dict[str, Any],
        persona_style: dict[str, Any],
    ) -> None:
        """为文字轨创建 text clip。

        2026-10-30（PRD text-animation-llm）：**主路线改为 prompt + LLM 生成**
        （`animation/text_generation.py`：6 类文字动效 + 字幕带避让），
        `AnimationCatalog` 的固定关键帧表降级为 fallback（LLM 失败/开关关闭时），
        并如实登记 `text_llm_failed` / `text_llm_fallback_template` 降级。
        """
        # 时长钳制——原 max(dur, 1.0) 让 0.2s 片段挂 1s 文字覆盖侵入下一
        # 场景；改为不越出宿主片段，下限 0.2s
        clip_duration = max(0.2, float(vid_clip.duration_sec or 0.2))
        text_content, text_source = self._extract_text_with_source(vid_clip, marker)
        if not text_content:
            text_content = marker.get("text", "")
            if text_content:
                text_source = "marker"

        # 长文本 + typewriter → 路由到字幕轨（CAPTION）
        # 仅当文本来自真实旁白（marker/metadata）时才允许进入字幕轨；
        # description 正则兜底提取的是场景描述（「混剪」/「镜头」-style），
        # 禁止路由到字幕轨避免污染，降级为普通 TEXT clip（保留入场动画）。
        # 长真实旁白 → 字幕轨（文字动画只做短语）。
        # 2026-10-30：不再依赖 `_is_typewriter_like`（它按关键帧属性判定，对裸 id
        # "typewriter" 会返回 False，导致长旁白被做成文字动画压在字幕上）。
        is_typewriter = self._is_typewriter_like(anim_id)
        if len(text_content) > 50 and text_source in ("marker", "metadata"):
            self._handle_caption(
                text_track, vid_clip, text_content, persona_style,
            )
            return

        # 生成 FFmpeg keyframes 供 drawtext 渲染
        # ── LLM 文字动画（2026-10-30 主路线）────────────────────────────
        # 与 38 个类型化 MG 同构：prompt 定义类型语义 + LLM 产出 MGDef，
        # 这里把它转成 drawtext 可消费的 keyframes。失败才回退模板表。
        if self._text_llm_enabled():
            llm_clip = await self._build_text_clip_llm(
                vid_clip, text_content, clip_duration, persona_style,
                anim_id=anim_id, anim_name=anim_name,
            )
            if llm_clip is not None:
                overlap = self._find_overlapping_clip(text_track, llm_clip)
                if overlap is not None:
                    logger.info("AnimationAgent: 跳过重叠文字动画 %s（与 %s 时间重叠）",
                                llm_clip.id[:8], overlap.id[:8])
                    return
                llm_clip.track_id = text_track.id
                text_track.clips.append(llm_clip)
                text_track.clips.sort(key=lambda c: c.start_sec)
                return
            _note(
                "text_llm_fallback_template",
                f"文字动画 LLM 生成失败，回退 AnimationCatalog 模板（anim_id={anim_id}）",
                scope=str((vid_clip.metadata or {}).get("scene_title") or ""),
                impact="该段文字动效是固定模板关键帧，不是按语义定制",
            )
            # 回退到同步模板入口（既有测试/调用方的稳定路径）
            self._handle_text_animation(
                text_track, vid_clip, anim_id, anim_name, marker, persona_style)
            return

        full_kfs = AnimationCatalog.build_full_keyframes(
            anim_id, vid_clip.start_sec, clip_duration
        )

        # 完整文字样式字段（统一默认 + persona_style 覆盖，任务 32）
        style = _merge_style_defaults(DEFAULT_TEXT_STYLE, persona_style)

        # 底部叠字修复（E2E 2026-10-30）：文字动画的正文往往就是旁白整句，
        # 而 audio 阶段的字幕也来自同一份旁白 → 成片底部出现两行字
        # （一行文字动画、一行字幕）。字幕负责"说出来的每一句"，文字动画只做
        # 短语/关键词强调。判据：内容等于所在片段的旁白且足够长（>10 字）→ 交给字幕。
        _narration = ""
        try:
            _narration = str(getattr(vid_clip, "text", "") or
                             (vid_clip.metadata or {}).get("narration_text") or "").strip()
        except Exception:  # noqa: BLE001 — 取不到旁白就按普通文字动画处理
            _narration = ""
        if _narration and len(text_content.strip()) > 10 and \
                text_content.strip() == _narration:
            logger.info(
                "AnimationAgent: 文字动画与旁白整句重复，交给字幕渲染（避免底部叠字）: %s",
                text_content[:30],
            )
            return

        text_clip = Clip(
            id=_uid("tc"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id=text_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=text_content or marker.get("text", ""),
            font="sans-serif",
            **style,
            keyframes=full_kfs,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "renderer": "drawtext",
                # 位置避让（同上）：字幕固定在 bottom，文字动画默认落在 center，
                # 只有 persona 显式声明了 text_anim_position 时才跟随。
                "position": (
                    persona_style.get("text_anim_position")
                    or ("center" if str(persona_style.get("position") or "bottom") == "bottom"
                        else persona_style.get("position"))
                ),
                "stroke_width": persona_style.get("stroke_width", 0),
                "stroke_color": persona_style.get("stroke_color", "#000000"),
            },
        )
        if is_typewriter:
            text_clip.metadata["typewriter"] = True

        # 启用重叠去重（_find_overlapping_clip 此前定义未调用）——
        # 同轨道文字片段时间重叠会叠字渲染
        overlap = self._find_overlapping_clip(text_track, text_clip)
        if overlap is not None:
            logger.info("AnimationAgent: 跳过重叠文字动画 %s（与 %s 时间重叠）",
                        text_clip.id[:8], overlap.id[:8])
            return

        text_track.clips.append(text_clip)
        text_track.clips.sort(key=lambda c: c.start_sec)

    def _handle_text_animation(
        self,
        text_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        marker: dict[str, Any],
        persona_style: dict[str, Any],
    ) -> None:
        """模板路径（同步）：AnimationCatalog 固定关键帧 → drawtext clip。

        生产路径请用 `_handle_text_animation_async`（LLM 优先，失败回退到这里）；
        保留同步入口是为了既有调用方与测试的稳定契约。
        """
        clip_duration = max(0.2, float(vid_clip.duration_sec or 0.2))
        text_content, text_source = self._extract_text_with_source(vid_clip, marker)
        if not text_content:
            text_content = marker.get("text", "")
            if text_content:
                text_source = "marker"
        is_typewriter = self._is_typewriter_like(anim_id)
        if len(text_content) > 50 and text_source in ("marker", "metadata"):
            self._handle_caption(text_track, vid_clip, text_content, persona_style)
            return
        full_kfs = AnimationCatalog.build_full_keyframes(
            anim_id, vid_clip.start_sec, clip_duration
        )

        # 完整文字样式字段（统一默认 + persona_style 覆盖，任务 32）
        style = _merge_style_defaults(DEFAULT_TEXT_STYLE, persona_style)

        # 底部叠字修复（E2E 2026-10-30）：文字动画的正文往往就是旁白整句，
        # 而 audio 阶段的字幕也来自同一份旁白 → 成片底部出现两行字
        # （一行文字动画、一行字幕）。字幕负责"说出来的每一句"，文字动画只做
        # 短语/关键词强调。判据：内容等于所在片段的旁白且足够长（>10 字）→ 交给字幕。
        _narration = ""
        try:
            _narration = str(getattr(vid_clip, "text", "") or
                             (vid_clip.metadata or {}).get("narration_text") or "").strip()
        except Exception:  # noqa: BLE001 — 取不到旁白就按普通文字动画处理
            _narration = ""
        if _narration and len(text_content.strip()) > 10 and \
                text_content.strip() == _narration:
            logger.info(
                "AnimationAgent: 文字动画与旁白整句重复，交给字幕渲染（避免底部叠字）: %s",
                text_content[:30],
            )
            return

        text_clip = Clip(
            id=_uid("tc"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id=text_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=text_content or marker.get("text", ""),
            font="sans-serif",
            **style,
            keyframes=full_kfs,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "renderer": "drawtext",
                # 位置避让（同上）：字幕固定在 bottom，文字动画默认落在 center，
                # 只有 persona 显式声明了 text_anim_position 时才跟随。
                "position": (
                    persona_style.get("text_anim_position")
                    or ("center" if str(persona_style.get("position") or "bottom") == "bottom"
                        else persona_style.get("position"))
                ),
                "stroke_width": persona_style.get("stroke_width", 0),
                "stroke_color": persona_style.get("stroke_color", "#000000"),
            },
        )
        if is_typewriter:
            text_clip.metadata["typewriter"] = True

        # 启用重叠去重（_find_overlapping_clip 此前定义未调用）——
        # 同轨道文字片段时间重叠会叠字渲染
        overlap = self._find_overlapping_clip(text_track, text_clip)
        if overlap is not None:
            logger.info("AnimationAgent: 跳过重叠文字动画 %s（与 %s 时间重叠）",
                        text_clip.id[:8], overlap.id[:8])
            return

        text_track.clips.append(text_clip)
        text_track.clips.sort(key=lambda c: c.start_sec)

    # ── 长文本字幕处理 ─────────────────────────────────────

    # ── 文字动画 LLM 路线（PRD text-animation-llm M2）────────────

    @staticmethod
    def _text_llm_enabled() -> bool:
        """文字动画是否走 LLM（`settings.text_animation_llm`，默认开）。"""
        try:
            from clipwright.config import settings

            return bool(getattr(settings, "text_animation_llm", True))
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _pick_text_type(text: str) -> str:
        """按文本语义挑文字动效类型（6 类里最贴的一个）。

        规则简单可解释（不额外花一次 LLM 调用）：
        - 含引号/「引」→ quote_block
        - 含数字（含中文数字量词）→ number_punch
        - 很短（≤8 字）→ emphasis_keyword
        - 很长（>14 字）→ title_stack（主副标题拆分）
        - 其余 → reveal_line
        """
        raw = str(text or "").strip()
        if any(mark in raw for mark in ("「", "」", "“", "”", '"', "『", "』")):
            return "quote_block"
        # 阿拉伯数字只在短句里当"冲屏数字"（长句里的数字不是主角）
        if re.search(r"\d", raw) and len(raw) <= 12:
            return "number_punch"
        if re.search(r"[一二三四五六七八九十百千]+\s*(分钟|小时|天|年|个|次)", raw) and len(raw) <= 8:
            return "number_punch"
        if 0 < len(raw) <= 6:
            return "emphasis_keyword"
        if len(raw) > 14:
            return "title_stack"
        return "reveal_line"

    @staticmethod
    def _mgdef_to_drawtext_keyframes(
        mg_def: dict[str, Any], *, clip_start: float,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """MGDef 元素 → drawtext keyframes（`{time, properties}`，绝对时间）+ 主元素样式。"""
        elements = [e for e in (mg_def.get("elements") or []) if isinstance(e, dict)]
        if not elements:
            return [], {}
        # 主元素：字号最大者（没有 font_size 时取第一个）
        def _size(elem: dict[str, Any]) -> float:
            try:
                return float(elem.get("font_size") or 0)
            except (TypeError, ValueError):
                return 0.0

        main = max(elements, key=_size)
        out: list[dict[str, Any]] = []
        for kf in (main.get("keyframes") or []):
            if not isinstance(kf, dict):
                continue
            try:
                t = float(kf.get("time") or 0)
            except (TypeError, ValueError):
                continue
            props: dict[str, Any] = {}
            for key in ("opacity", "scale", "translate_x", "translate_y",
                        "font_size", "color", "letter_spacing", "rotate", "easing"):
                if key in kf and kf[key] is not None:
                    props[key] = kf[key]
            if not props:
                continue
            out.append({"time": round(clip_start + t, 3), "properties": props})
        out.sort(key=lambda item: item["time"])
        style: dict[str, Any] = {}
        for key in ("font_size", "color", "letter_spacing", "text_shadow"):
            if main.get(key) is not None:
                style[key] = main[key]
        style["text"] = str(main.get("content") or main.get("text") or "")
        return out, style

    async def _build_text_clip_llm(
        self,
        vid_clip: Clip,
        text_content: str,
        clip_duration: float,
        persona_style: dict[str, Any],
        *,
        anim_id: str,
        anim_name: str,
    ) -> Clip | None:
        """LLM 生成文字动画 clip；失败返回 None（调用方回退模板并登记降级）。"""
        from clipwright.animation import text_generation as tg

        type_id = self._pick_text_type(text_content)
        try:
            result = await tg.generate_for_type(
                type_id,
                text=text_content or "",
                duration_s=clip_duration,
                persona=persona_style,
                caption_band=True,
            )
        except Exception as exc:  # noqa: BLE001 — 生成失败走模板
            _note("text_llm_failed", f"{type(exc).__name__}: {exc}",
                  scope=str((vid_clip.metadata or {}).get("scene_title") or ""),
                  impact="该段文字动效回退模板关键帧")
            return None
        if not result.ok:
            _note("text_llm_failed", result.error or "生成失败",
                  scope=str((vid_clip.metadata or {}).get("scene_title") or ""),
                  impact="该段文字动效回退模板关键帧")
            return None

        keyframes, main_style = self._mgdef_to_drawtext_keyframes(
            result.mg_def, clip_start=vid_clip.start_sec,
        )
        if not keyframes:
            _note("text_llm_failed", "LLM 产物没有可用关键帧",
                  impact="该段文字动效回退模板关键帧")
            return None

        # 动效覆盖度：最后一个关键帧相对片段的占比（<45% 记 text_motion_weak）
        span = max(1e-3, float(clip_duration))
        last_rel = max(0.0, keyframes[-1]["time"] - vid_clip.start_sec)
        coverage = min(1.0, last_rel / span)
        if coverage < 0.45:
            _note(
                "text_motion_weak",
                f"文字动效关键帧覆盖 {coverage:.0%}（<45%），后段接近静止",
                scope=str((vid_clip.metadata or {}).get("scene_title") or ""),
                impact="观众会觉得这行字只是静态贴图",
            )

        style = _merge_style_defaults(DEFAULT_TEXT_STYLE, persona_style)
        if main_style.get("font_size") is not None:
            style["font_size"] = main_style["font_size"]
        # 文字动画是"画面主体"，字号有下限：LLM 偶尔给 28 这种字幕级字号，
        # 1080p 成片里几乎看不见（E2E 2026-10-30 实测）。字幕有自己的一套样式，
        # 这里只保证文字层在 1080p 基准下可读（1440/720 按比例另有缩放）。
        try:
            _min_size = 56.0 * (float(self._tl_width or 1920) / 1920.0)
            style["font_size"] = max(float(style.get("font_size") or 0), _min_size)
        except (TypeError, ValueError):
            style["font_size"] = max(float(style.get("font_size") or 0), 56.0)
        if main_style.get("color"):
            style["font_color"] = main_style["color"]
        if main_style.get("letter_spacing") is not None:
            style["letter_spacing"] = main_style["letter_spacing"]

        return Clip(
            id=_uid("tc"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id="",  # 由调用方 append 到 text_track，track_id 在构造后补
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=main_style.get("text") or text_content,
            font="sans-serif",
            **style,
            keyframes=keyframes,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "renderer": "drawtext",
                "position": "center",  # 字幕带避让（caption 固定 bottom）
                "text_anim_type": type_id,
                "text_llm_method": result.method,
                "text_prompt_version": result.prompt_version,
                "avoid_caption_band_px": tg.CAPTION_BAND_PX,
                "stroke_width": persona_style.get("stroke_width", 0),
                "stroke_color": persona_style.get("stroke_color", "#000000"),
            },
        )

    @staticmethod
    def _handle_caption(
        text_track: Track,
        vid_clip: Clip,
        text_content: str,
        persona_style: dict[str, Any],
    ) -> None:
        """长文本 → CAPTION clip，使用 FFmpeg drawtext 渲染。"""
        # 与文字动画同样钳制，不越出宿主片段
        clip_duration = max(0.2, float(vid_clip.duration_sec or 0.2))
        # 完整字幕样式字段（统一默认 + persona_style 覆盖，任务 32）
        style = _merge_style_defaults(DEFAULT_CAPTION_STYLE, persona_style)
        caption_clip = Clip(
            id=_uid("cc"),
            kind=ClipKind.CAPTION,
            asset_id="",
            track_id=text_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=text_content,
            font="sans-serif",
            **style,
            metadata={
                "category": "caption",
                "position": persona_style.get("position", "bottom"),
                "renderer": "drawtext",
            },
        )
        text_track.clips.append(caption_clip)
        text_track.clips.sort(key=lambda c: c.start_sec)

    # ── 逻辑动画处理 ──────────────────────────────────────

    async def _handle_logic_animation(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        marker: dict[str, Any],
        persona_style: dict[str, Any] | None = None,
    ) -> None:
        """在动画轨创建独立的逻辑动画 clip。优先 Hyperframes 渲染 SVG，不可用时降级到 drawtext。

        MG 动画（ID 以 mg_ 开头）使用 MGRenderer 渲染 HTML/CSS 动画。"""
        text_content = marker.get("text", self._extract_text_content(vid_clip, marker))
        # 防御：旧 marker 的 text 可能是原始 JSON 串，先剥离
        text_content = self._strip_json_payload(text_content)
        if not text_content:
            text_content = "逻辑关系"
        duration = min(vid_clip.duration_sec, 6.0)

        # ── mg_dynamic：自由形式 LLM MG（结构 Agent 直接给描述/文本），保持既有路径。
        # 它没有固定类型提示词，属于"按需描述生成"，与 38 个类型化路径并存。
        if anim_id == "mg_dynamic":
            await self._handle_llm_mg(
                anim_track, vid_clip, anim_id, anim_name,
                text_content, duration, marker, persona_style,
            )
            return

        # ── 主路径（用户 2026-10-29）：所有逻辑动画按「类型提示词 + 视频类型 + persona」
        # 由 LLM 生成 mg_def（不再是静态 SVG 贴图，也不是统一塞给 mg_dynamic）。
        if self._typed_generation_enabled():
            typed_ok = await self._handle_typed_animation(
                anim_track, vid_clip, anim_id, anim_name, text_content, duration,
                marker, persona_style,
            )
            if typed_ok:
                return

        # ── 回退 1：mg_* 内置模板（仍是动态 HTML/CSS，只是非 LLM 定制）
        if anim_id.startswith("mg_"):
            _note("animation_typed_fallback",
                  f"类型化 LLM 生成未生效，{anim_id} 回退内置模板 mg_*",
                  scope=str(marker.get("scene_title") or anim_id),
                  impact="该段动画非按类型提示词定制，构图与文案贴合度下降")
            await self._handle_mg_animation(
                anim_track, vid_clip, anim_id, anim_name,
                text_content, duration, marker, persona_style,
            )
            return

        # ── 回退 2：图解 SVG（现已补真实关键帧，见 diagram_svg；仍是动的）
        _note("animation_svg_fallback",
              f"{anim_id} 无可用类型化路径，回退图解 SVG",
              scope=str(marker.get("scene_title") or anim_id),
              impact="该段只有矢量图解 + 关键帧，缺少 MG 级动效设计")
        await self._handle_svg_fallback(
            anim_track, vid_clip, anim_id, anim_name, text_content, duration, persona_style,
        )

    # ── 类型化 LLM 生成（主路径） ─────────────────────────

    def _typed_generation_enabled(self) -> bool:
        """类型化生成开关（settings.animation_llm_generation，默认开）。"""
        try:
            from clipwright.config import settings

            enabled = bool(getattr(settings, "animation_llm_generation", True))
        except Exception:  # noqa: BLE001
            enabled = True
        if enabled and not getattr(self, "_type_registry_checked", False):
            # 首次启用时做一次注册表自检：缺提示词/fallback 不合法只告警不阻断，
            # 免得一个漏写的 md 让整部片子没有动画。
            self._type_registry_checked = True
            try:
                from clipwright.animation.type_specs import validate_registry

                problems = validate_registry()
                if problems:
                    logger.warning("AnimationAgent: 动画类型注册表自检: %s", "；".join(problems))
            except Exception:  # noqa: BLE001
                logger.warning("AnimationAgent: 动画类型注册表自检失败", exc_info=True)
        return enabled

    def _category_anim_context(self):
        """从 category 插件上下文提取动画白名单字段（不把整份配置塞进提示词）。"""
        from clipwright.animation.type_specs import CategoryAnimationContext

        ctx = getattr(self, "_mg_category_context", {}) or {}
        notes: list[str] = []
        for key in ("mg_style_guidance", "stage_prompt"):
            val = ctx.get(key)
            if isinstance(val, str) and val.strip():
                notes.append(val.strip()[:400])
        brief_style = ctx.get("brief_animation_style")
        if isinstance(brief_style, dict) and brief_style:
            notes.append("简报动画风格：" + json.dumps(brief_style, ensure_ascii=False)[:300])
        return CategoryAnimationContext(
            plugin_id=str(ctx.get("plugin_id") or ""),
            tone=str(ctx.get("tone") or ""),
            density=str(ctx.get("pacing") or ""),
            audience=str(ctx.get("display_name") or ""),
            notes=tuple(notes),
        )

    def _persona_anim_context(self, persona_style: dict[str, Any] | None):
        """从 persona 风格 + persona prompt 提取动画白名单字段。"""
        from clipwright.animation.type_specs import PersonaAnimationContext

        style = persona_style or {}
        palette = tuple(
            str(style[k]) for k in ("primary_color", "secondary_color", "accent_color", "font_color")
            if style.get(k)
        )
        guide = str(getattr(self, "_persona_prompt", "") or "")
        # PRD §16 R5：统一上限（旧实现 400/600/1500/2000 四处不同截断）
        from clipwright.services.visual_context import DEFAULT_PERSONA_PROMPT_MAX
        try:
            from clipwright.config import settings as _settings
            _limit = int(getattr(_settings, "persona_prompt_max_chars", DEFAULT_PERSONA_PROMPT_MAX))
        except Exception:  # noqa: BLE001
            _limit = DEFAULT_PERSONA_PROMPT_MAX
        desc = " ".join(x for x in (str(style.get("style_description") or ""), guide[:_limit]) if x)
        return PersonaAnimationContext(
            persona_id=str(style.get("persona_id") or style.get("name") or ""),
            palette=palette,
            font=str(style.get("font") or ""),
            motion_speed=str(style.get("motion_speed") or ""),
            style_description=desc,
        )

    async def _handle_typed_animation(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        text_content: str,
        duration: float,
        marker: dict[str, Any],
        persona_style: dict[str, Any] | None = None,
    ) -> bool:
        """按类型生成动画；返回 True 表示已创建 clip（调用方不再回退）。"""
        from clipwright.animation.mg.type_generation import fingerprint_of

        if not await self._hyperframes_available():
            logger.warning("AnimationAgent: Hyperframes 不可用，类型化动画 [%s] 回退", anim_name)
            return False

        spec = self._resolve_type_spec(anim_id)
        if spec is None:
            logger.warning("AnimationAgent: 未注册的动画类型 %s，回退", anim_id)
            return False

        # 同片去重：同类型同内容只播一次（用户要求「同片不重复播」）。
        # 逻辑动画是并发执行的，check-and-set 必须串行化，否则同内容会被并发生成两次。
        fp = fingerprint_of(spec.id, text_content)
        seen = getattr(self, "_anim_fingerprints", None)
        if seen is None:
            seen = {}
            self._anim_fingerprints = seen
        lock = getattr(self, "_anim_fp_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._anim_fp_lock = lock
        async with lock:
            if fp in seen:
                prev = seen[fp]
                logger.info(
                    "AnimationAgent: 同片重复动画跳过 %s（内容已出现过 @ %.1fs，本次 %.1fs）",
                    anim_name, prev.get("start", 0.0), vid_clip.start_sec,
                )
                self._add_trace_warning(
                    f"动画内容重复已跳过：{anim_name}（此前出现在 {prev.get('start', 0.0):.1f}s）"
                )
                return True  # 视为已处理（跳过重复也是有意的结果）
            seen[fp] = {"start": float(vid_clip.start_sec), "type": spec.id}

        try:
            from clipwright.animation.mg.generator import MGGenerator

            mg_gen = MGGenerator()
        except Exception as e:  # noqa: BLE001
            logger.warning("AnimationAgent: 类型化生成器初始化失败: %s", e)
            return False

        payload = marker.get("payload") if isinstance(marker, dict) else None
        web_context = ""
        if re.search(_DATA_FACT_RE, text_content):
            try:
                search_svc = WebSearchService()
                if search_svc.is_configured():
                    results = await search_svc.search(text_content[:120])
                    web_context = "\n".join(
                        f"标题: {r.get('title', '')}\n摘要: {r.get('snippet', '')}" for r in (results or [])[:3]
                    )
            except Exception:  # noqa: BLE001 — 搜索失败不影响生成
                web_context = ""

        scene_meta = vid_clip.metadata or {}
        prior = [
            f"{info.get('type')}@ {info.get('start', 0.0):.0f}s" for info in seen.values()
            if info.get("start") != vid_clip.start_sec
        ]
        try:
            result = await mg_gen.generate_for_type(
                spec=spec,
                content=text_content,
                duration_s=duration,
                category=self._category_anim_context(),
                persona=self._persona_anim_context(persona_style),
                persona_style=persona_style or {},
                scene_context={
                    "title": scene_meta.get("title", ""),
                    "keywords": scene_meta.get("keywords", []),
                    "description": scene_meta.get("description", ""),
                },
                category_context=getattr(self, "_mg_category_context", {}) or {},
                vision_prompt=getattr(self, "_vision_prompt", "") or "",
                web_context=web_context,
                prior_fingerprints=prior,
                width=self._tl_width,
                height=self._tl_height,
                fps=self._tl_fps,
                pipeline_id=getattr(self, "_pid", None),
            )
        except Exception as e:  # noqa: BLE001 — 任何异常都必须能回退
            logger.exception("AnimationAgent: 类型化生成异常 %s: %s", anim_name, e)
            return False

        if not result.ok or not result.html:
            logger.warning(
                "AnimationAgent: 类型化生成未通过门禁 %s（type=%s）：%s",
                anim_name, spec.id, "；".join(result.issues[:3]) or result.method,
            )
            self._add_trace_warning(
                f"类型化生成未通过，回退：{anim_name}（{spec.id}）" +
                (f" — {result.issues[0]}" if result.issues else "")
            )
            return False

        clip_dur = min(float(result.mg_def.get("duration_sec") or duration), vid_clip.duration_sec)
        anim_clip = Clip(
            id=_uid("tmg"),
            kind=ClipKind.ANIMATION,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_dur,
            text=text_content,
            metadata={
                "anim_type": spec.id,
                "anim_name": spec.name or anim_name,
                "category": "mg_dynamic",       # 渲染链路按 renderer 判断；此值保持兼容
                "logic_category": "logic",       # 语义来源：逻辑动画
                "renderer": "mg_hyperframes",
                "mg_html": result.html,
                "mg_def": result.mg_def,
                "mg_params": (result.raw or {}).get("params") or {},
                "mg_method": result.method,
                "type_prompt_version": result.prompt_version,
                "motion": result.motion,
                "type_issues": result.issues[:6],
                # 可读性硬门禁的自动修复记录（补了哪些 text_shadow）
                "readability_fix": getattr(result, "readability_fix", []) or [],
                "persona_fingerprint": (persona_style or {}).get("persona_fingerprint"),
                "persona_source": (persona_style or {}).get("persona_source"),
                "position": "center",
            },
        )
        if self._find_overlapping_clip(anim_track, anim_clip) is not None:
            logger.info("AnimationAgent: 动画窗口冲突，跳过 %s（同窗口已有动画）", anim_name)
            return True
        anim_track.clips.append(anim_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)
        logger.info(
            "AnimationAgent: [类型化LLM]%s → type=%s, method=%s, html=%d chars, moving=%s",
            anim_name, spec.id, result.method, len(result.html),
            result.motion.get("moving_count"),
        )
        return True

    def _resolve_type_spec(self, anim_id: str):
        """解析动画类型规格；未知类型用通用 spec 兜底（不阻塞生成）。"""
        from clipwright.animation.type_specs import AnimationTypeSpec, get_spec

        spec = get_spec(anim_id)
        if spec is not None:
            return spec
        try:
            from clipwright.animation.catalog import AnimationCatalog

            for item in AnimationCatalog.get_logic_animations():
                if str(item.get("id")) == anim_id:
                    return AnimationTypeSpec(
                        id=anim_id,
                        name=str(item.get("name") or anim_id),
                        intent=str(item.get("desc") or anim_id),
                    )
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _handle_svg_fallback(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        text_content: str,
        duration: float,
        persona_style: dict[str, Any] | None = None,
    ) -> None:
        """回退 2：图解 SVG（`diagram_svg.py`）。

        改造后 SVG 自带真实 CSS 关键帧（入场 + 持续动作 + 退场），不再是静态贴图。
        仅在类型化 LLM 生成失败 / 开关关闭 / Hyperframes 不可用时走到这里。
        """
        diagram_params = self._build_diagram_params(anim_id, text_content, duration)

        diagram_style = {}
        if persona_style:
            diagram_style = {
                "primary_color": persona_style.get("primary_color", "#4f8cff"),
                "secondary_color": persona_style.get("secondary_color", "#ff6b6b"),
                "accent_color": persona_style.get("accent_color", "#fbbf24"),
                "text_color": persona_style.get("font_color", "#ffffff"),
                "font_size": persona_style.get("font_size", 36),
            }

        # ── 创建逻辑动画 clip（非 MG） ──
        # 异步探测 Hyperframes 是否可用（含冷启动等待，最多 120s），
        # 避免首个 pipeline 因缓存探针冷启动返回 False 而误降级为静态 drawtext。
        # 只有真正等待超时（不可用）才走降级分支。
        hf_available = await self._hyperframes_available()
        renderer = "hyperframes" if hf_available else "drawtext"
        if not hf_available:
            # 降级：Hyperframes 不可用 → 跳过创建 clip，绝不把 LLM 生成的动画
            # 设计描述（text_content）打印成屏幕文字（修复前会产生
            # "因果: 镜像→他者→…" 的字幕污染）。仅记录 warning + trace 事件。
            logger.warning(
                "AnimationAgent: Hyperframes 不可用，逻辑动画 [%s] 已跳过"
                "（不再降级为文字显示）", anim_name,
            )
            try:
                from clipwright.services.trace import add_event as _evt
                _evt(getattr(self, '_pid', ''), "animation", "warning",
                     f"Hyperframes 不可用，[逻辑动画]{anim_name} 已跳过",
                     {"anim_id": anim_id, "degradation": "hyperframes_not_available",
                      "skipped": True})
            except Exception:
                pass
            return

        anim_clip = Clip(
            id=_uid("lc"),
            kind=ClipKind.ANIMATION,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=duration,
            text=text_content,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "category": "logic",
                "diagram_params": diagram_params,
                "diagram_style": diagram_style,
                "renderer": renderer,
                "font_size": 48,
                "font_color": diagram_style.get("text_color", "#ffffff"),
                "position": "center",
            },
        )
        # P2-35: 共享动画轨窗口冲突去重——同窗口已有动画时跳过新片段
        # （旧实现仅 text 轨做重叠检测，logic/MG/LLM-MG 三处直接 append
        #  造成同屏动画互相覆盖）
        if self._find_overlapping_clip(anim_track, anim_clip) is not None:
            logger.info("AnimationAgent: 动画窗口冲突，跳过 %s（同窗口已有动画）", anim_name)
        else:
            anim_track.clips.append(anim_clip)
            anim_track.clips.sort(key=lambda c: c.start_sec)

    # ── MG 动画处理 ──────────────────────────────────────

    async def _handle_mg_animation(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        text_content: str,
        duration: float,
        marker: dict[str, Any],
        persona_style: dict[str, Any] | None = None,
    ) -> None:
        """处理 MG 动画标记 — 通过 MGRenderer 生成 HTML 动画。"""
        from clipwright.animation.mg.fallback import FallbackEngine
        from clipwright.animation.mg_renderer import MGRenderer

        # 防御：旧时间线中 marker["text"] 可能是原始 JSON 串，
        # 解析出 text/description，避免 JSON 片段被渲染为屏上大字。
        text_content = self._strip_json_payload(text_content)

        # 加载 MG 动画定义
        mg_def = MGRenderer.load_animation(anim_id)
        if mg_def is None:
            # 降级：MG 定义缺失 → 跳过创建 clip，不把设计描述打印成屏幕文字
            logger.warning("MG 动画未找到: %s，已跳过（不再降级为文字显示）", anim_id)
            self._add_trace_warning(f"MG 动画未找到: {anim_id}，动画已跳过")
            return

        # 背景守卫：模板 MG 与 LLM MG 一致——vision_prompt 为空时强制 bg 透明，
        # 动画叠加在实拍素材上，不允许不透明全幅背景（用户要求，T3 补充覆盖模板路径）
        from clipwright.animation.mg.generator import MGGenerator
        mg_def = MGGenerator._ensure_no_background(
            mg_def, getattr(self, "_vision_prompt", "") or ""
        )

        # 解析输入参数: 格式 "文字|副标题|值"，按模板实际 params 定义按位置填充
        # （双保护(d): 不硬编码 text/value/unit/subtitle 4 键 — 固定模板各有不同
        # 参数键，如 mg_comparison_split 的 left/right/left_sub/right_sub/vs/accent）
        param_defs = mg_def.get("params", {})
        param_keys = list(param_defs.keys())
        if not param_keys:
            # 无 params 声明 → 从元素内容扫描占位符
            seen: list[str] = []
            for m in re.findall(
                r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", json.dumps(mg_def, ensure_ascii=False)
            ):
                if m not in seen:
                    seen.append(m)
            param_keys = seen

        mg_params: dict[str, str] = {}
        if text_content:
            parts = FallbackEngine.extract_keywords(text_content)
            for i, key in enumerate(param_keys):
                if i < len(parts):
                    mg_params[key] = parts[i]
                else:
                    default = param_defs.get(key)
                    mg_params[key] = default.get("default", "") if isinstance(default, dict) else ""
            if parts and "text" not in param_keys:
                mg_params["text"] = parts[0]

        style = persona_style or {}
        if "primary_color" in style and "accent" in mg_params:
            # Persona 主色覆盖默认 accent
            mg_params["accent"] = style["primary_color"]
        # PRD §16 R6：模板/插件路径也要能看到 persona（此前只拿到一个 accent 颜色）。
        # style_hint 是给人读的一行摘要：配色/palette/动画风格/节奏；模板若声明
        # {style_hint} 占位符即可直接渲染，未声明时仅进入 metadata 与 trace。
        style_hint = str(style.get("style_hint") or "").strip()
        if style_hint:
            mg_params.setdefault("style_hint", style_hint)

        mg_dur = mg_def.get("duration_sec", duration)
        # P1-03: 场景窗口短于 MG 自身时长时跳过——旧行为把 clip 钳到窗口但 HTML
        # 仍按自身时长渲染，退场动画被硬切丢失且无重新配速。跳过是可预期行为。
        if vid_clip.duration_sec < float(mg_dur) - 0.05:
            self._add_trace_warning(
                f"MG 动画 {anim_id} 时长 {mg_dur}s 超过场景窗口 "
                f"{vid_clip.duration_sec:.2f}s，已跳过（窗口不足）"
            )
            return
        clip_dur = min(mg_dur, vid_clip.duration_sec)

        # 渲染为 HTML（时间线尺寸优先，动态解析分辨率/帧率）
        try:
            html = MGRenderer.render(
                mg_def, mg_params,
                width=self._tl_width, height=self._tl_height, fps=self._tl_fps,
            )
        except Exception as e:
            # 降级：渲染失败 → 跳过创建 clip，不把设计描述打印成屏幕文字
            logger.warning("MG 动画渲染失败: %s，已跳过（不再降级为文字显示）", e)
            self._add_trace_warning(f"MG 动画渲染失败: {anim_id}，动画已跳过")
            return

        anim_clip = Clip(
            id=_uid("mg"),
            kind=ClipKind.ANIMATION,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_dur,
            text=text_content,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "category": "mg",
                "renderer": "mg_hyperframes",
                "mg_html": html,
                "mg_def": mg_def,
                "mg_params": mg_params,
                "position": "center",
            },
        )
        # P2-35: 共享动画轨窗口冲突去重——同窗口已有动画时跳过新片段
        # （旧实现仅 text 轨做重叠检测，logic/MG/LLM-MG 三处直接 append
        #  造成同屏动画互相覆盖）
        if self._find_overlapping_clip(anim_track, anim_clip) is not None:
            logger.info("AnimationAgent: 动画窗口冲突，跳过 %s（同窗口已有动画）", anim_name)
        else:
            anim_track.clips.append(anim_clip)
            anim_track.clips.sort(key=lambda c: c.start_sec)
        logger.info("AnimationAgent: [MG动画]%s → HTML已生成 (%s)", anim_name, anim_id)

    # ── LLM 动态 MG 动画处理 ──────────────────────────────

    async def _handle_llm_mg(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        text_content: str,
        duration: float,
        marker: dict[str, Any],
        persona_style: dict[str, Any] | None = None,
    ) -> None:
        """处理 mg_dynamic 标记 — 通过内置 llm_mg 引擎动态生成 MG 动画。"""
        # LLM MG 产物依赖 Hyperframes 渲染，入口同样用异步探测（含冷启动等待）。
        # 只有真正等待超时（不可用）才走降级分支（跳过，不降级为文字显示）。
        if not await self._hyperframes_available():
            logger.warning("AnimationAgent: Hyperframes 不可用，mg_dynamic 跳过")
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        try:
            from clipwright.animation.mg import MGGenerator
            mg_gen = MGGenerator()
        except Exception as e:
            logger.warning("AnimationAgent: llm_mg 引擎初始化失败: %s", e)
            mg_gen = None

        if mg_gen is None:
            logger.warning("AnimationAgent: llm_mg 引擎不可用，mg_dynamic 跳过")
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        # 解析 JSON payload：优先 catalog 已解析的结构化字段（marker["payload"]），
        # 否则把 text_content 当 JSON 串解析（旧 marker 兼容）
        description = text_content
        text_parts = ""
        payload = marker.get("payload") if marker else None
        if isinstance(payload, dict):
            description = payload.get("description", "") or text_content
            text_parts = payload.get("text", "") or ""
        elif text_content and text_content.strip().startswith("{"):
            try:
                import json as _json
                payload = _json.loads(text_content.strip())
                description = payload.get("description", text_content)
                text_parts = payload.get("text", "")
            except Exception:
                pass  # 解析失败则使用原始值

        scene_meta = vid_clip.metadata or {}
        scene_context = {
            "title": scene_meta.get("title", ""),
            "keywords": scene_meta.get("keywords", []),
            "description": scene_meta.get("description", ""),
        }

        # 图片语义索引注入 mg_dynamic 生成 prompt — 让 LLM 理解图片内容后
        # 主动选择语义匹配的图片放入动画 image 元素（防图标语义错配）
        gen_description = description
        if getattr(self, "_image_index", None):
            gen_description = description + self._format_image_index_prompt(self._image_index)

        # 数据/事实型 mg_dynamic → 联网搜索，注入事实上下文
        # （门控：描述命中数据/事实关键词 AND WebSearchService 已配置才搜索；
        #  任何异常/空结果一律静默回退 web_context=""，行为与未配置时完全一致）
        web_context = ""
        if re.search(_DATA_FACT_RE, gen_description):
            try:
                search_svc = WebSearchService()
                if search_svc.is_configured():
                    results = await search_svc.search(gen_description[:120])
                    web_context = "\n".join(
                        f"标题: {r.get('title', '')}\n摘要: {r.get('snippet', '')}\n"
                        f"来源: {r.get('url', '')}"
                        for r in (results or [])[:3]
                    )
            except Exception:
                logger.warning("AnimationAgent: 联网搜索失败，跳过 web_context", exc_info=True)
                web_context = ""

        # 问题3修复：Persona prompt（prompt.md 风格指引）注入 MG 生成描述——
        # 此前 _persona_prompt 只捕获未使用，Zam 的动画风格（逐字出现/硬切闪现/
        # 冷色调+高对比/警示黄深红标注）完全没进动画生成。
        persona_style = persona_style or {}
        persona_guide = getattr(self, "_persona_prompt", "") or ""
        if persona_guide:
            style_hint = persona_style.get("style_description", "")
            gen_description = (
                f"{gen_description}\n\n## 创作者 Persona 动画风格指引（最高优先级）\n"
                f"{persona_guide[:2000]}"
                + (f"\n\n## 视觉风格摘要\n{style_hint}" if style_hint else "")
            )

        try:
            # P2-B 多方案择优：新引擎提供 generate_best（variants=1 时等价旧行为）；
            # 旧版/插件提供的引擎可能只有 generate → 回退调用，保持兼容。
            _generate = getattr(mg_gen, "generate_best", None) or mg_gen.generate
            result = await _generate(
                description=gen_description,
                text_content=text_parts or text_content,
                persona_style=persona_style or {},
                scene_context=scene_context,
                category_context=self._mg_category_context,
                vision_prompt=getattr(self, "_vision_prompt", "") or "",
                web_context=web_context,
                width=self._tl_width, height=self._tl_height, fps=self._tl_fps,
            )
        except Exception as e:
            logger.exception("AnimationAgent: llm_mg.generate() 异常: %s", e)
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        if not result.get("success"):
            logger.warning("AnimationAgent: llm_mg 生成失败 (method=%s)", result.get("method"))
            self._add_trace_warning(
                f"LLM MG 生成失败，使用降级方案: {result.get('fallback_template', 'none')}")

        html = result.get("html", "")
        mg_def = result.get("mg_def", {})
        method = result.get("method", "unknown")
        generation_id = result.get("generation_id", "")

        if not html:
            self._create_fallback_text_clip(anim_track, vid_clip, anim_name, text_content, duration)
            return

        clip_dur = min(mg_def.get("duration_sec", duration), vid_clip.duration_sec)

        anim_clip = Clip(
            id=_uid("mgd"),
            kind=ClipKind.ANIMATION,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_dur,
            # 存干净文本（text_parts 或 description），绝不存原始 JSON
            text=text_parts or description,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "category": "mg_dynamic",
                "renderer": "mg_hyperframes",
                "mg_html": html,
                "mg_def": mg_def,
                # V3：LLM 路径此前不存参数 → PIL 快路径无法安全复现（占位符未解析），
                # 只能保守回退 Chrome。这里把生成期实际使用的参数存下来。
                "mg_params": result.get("params") or {},
                "mg_method": method,
                "mg_generation_id": generation_id,
                "mg_fallback_template": result.get("fallback_template"),
                "position": "center",
            },
        )
        # P2-35: 共享动画轨窗口冲突去重——同窗口已有动画时跳过新片段
        # （旧实现仅 text 轨做重叠检测，logic/MG/LLM-MG 三处直接 append
        #  造成同屏动画互相覆盖）
        if self._find_overlapping_clip(anim_track, anim_clip) is not None:
            logger.info("AnimationAgent: 动画窗口冲突，跳过 %s（同窗口已有动画）", anim_name)
        else:
            anim_track.clips.append(anim_clip)
            anim_track.clips.sort(key=lambda c: c.start_sec)
        logger.info("AnimationAgent: [LLM MG]%s → method=%s, html=%d chars",
                     anim_name, method, len(html))
        self._llm_mg_generated += 1

    # ── A7: 图片素材库语义索引 ────────────────────────────────

    async def _search_library_images(
        self, topic: str, creative_brief: Any = None, top_k: int = 4,
    ) -> list[dict[str, Any]]:
        """D5: 主动从素材库检索图片（含 AI 生成图片源），供语义索引与 LLM 选图。

        查询词取 topic + 简报风格描述（最多 2 个查询）；local_path 直用，
        url-only 资产经 _cache_remote_image 落地后入索引（best-effort，失败跳过）。
        任何异常都不阻塞动画阶段（返回 []）。
        """
        try:
            from clipwright.material.registry import MaterialRegistry
            if not MaterialRegistry.list():
                return []
            queries = [q for q in (str(topic or "").strip(),) if q]
            if isinstance(creative_brief, dict):
                style = str(creative_brief.get("style_direction", "") or "").strip()
                if style:
                    queries.append(style[:60])
            results: list[Any] = []
            for q in queries[:2]:
                results.extend(await MaterialRegistry.search(q, top_k_per_source=top_k))
            entries: list[dict[str, Any]] = []
            for r in results:
                asset = getattr(r, "asset", None)
                if asset is None:
                    continue
                a_type = getattr(asset, "type", "")
                if str(getattr(a_type, "value", a_type)) != "image":
                    continue
                path = getattr(asset, "local_path", None) or ""
                if not path:
                    path = await self._cache_remote_image(getattr(asset, "url", "") or "")
                if not path:
                    continue
                entries.append({
                    "path": str(path),
                    "tags": [str(t) for t in (getattr(asset, "tags", None) or [])][:6],
                    "description": str(getattr(asset, "title", "") or ""),
                })
            return entries
        except Exception:
            logger.exception("AnimationAgent: 素材库图片检索失败（跳过）")
            return []

    @staticmethod
    async def _cache_remote_image(url: str) -> str:
        """下载远程图片到 _cache/ 供视觉分析（经 SSRF 校验，失败返回空串）。"""
        if not url:
            return ""
        try:
            from clipwright.security import assert_public_url
            assert_public_url(url)
            dest = Path("_cache") / ("anim_img_" + hashlib.sha256(url.encode()).hexdigest()[:16])
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
            return str(dest)
        except Exception:
            return ""

    async def _build_image_semantic_index(
        self, image_assets: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对图片资产逐个调用视觉分析器，构建语义索引 {path, tags, description}。

        优先级：分析器结果 > 资产自带 tags/description > 文件名标签（保底）。
        非 dict / 缺 path / 空 path 的资产直接跳过（malformed_input 对抗）。
        """
        from clipwright.services.vision import VisionService

        analyzer = VisionService()
        index: list[dict[str, Any]] = []
        for asset in image_assets or []:
            if not isinstance(asset, dict):
                continue
            path = asset.get("path") or asset.get("src") or ""
            if not isinstance(path, str) or not path.strip():
                continue
            entry: dict[str, Any] = {"path": path}
            tags: Any = None
            description: Any = None
            try:
                result = await analyzer.analyze_image(path)
                if isinstance(result, dict):
                    tags = result.get("tags")
                    description = result.get("description")
            except Exception:
                logger.debug("AnimationAgent: 图片分析失败，回退文件名标签: %s", path)
                tags, description = None, None
            if not tags:
                tags = asset.get("tags")
            if not description:
                description = asset.get("description")
            if not tags:
                tags = self._filename_tags(path)
            if not description:
                description = f"文件: {Path(path).name}"
            entry["tags"] = [str(t) for t in tags][:6] if isinstance(tags, list) else [str(tags)]
            entry["description"] = str(description)
            index.append(entry)
        return index

    @staticmethod
    def _filename_tags(path: str) -> list[str]:
        """从文件名提取标签（分析失败时的保底）。"""
        stem = Path(path).stem
        return [p for p in re.split(r"[\s_\-]+", stem) if len(p) > 2 and not p.isdigit()][:6]

    @staticmethod
    def _format_image_index_prompt(image_index: list[dict[str, Any]]) -> str:
        """把图片语义索引格式化为 mg_dynamic 生成 prompt 的选图段落。"""
        lines: list[str] = []
        for i, entry in enumerate(image_index or [], 1):
            tags = "、".join(str(t) for t in (entry.get("tags") or []))
            lines.append(
                f"{i}. 路径: {entry.get('path', '')}\n"
                f"   标签: {tags}\n"
                f"   语义描述: {entry.get('description', '')}"
            )
        return (
            "\n\n## 可用图片列表（含语义描述）\n"
            "选择语义匹配的图片放入动画 image 元素：\n"
            + "\n".join(lines)
        )

    def _create_fallback_text_clip(
        self,
        anim_track: Track,
        vid_clip: Clip,
        anim_name: str,
        text_content: str,
        duration: float,
    ) -> None:
        """降级占位：文本降级已禁用——不再把动画设计描述打印为屏幕文字，直接跳过。

        保留函数签名与全部调用点，但不再创建任何 TEXT/drawtext clip：
        仅记录 warning 日志 + trace 警告事件。
        """
        logger.warning(
            "AnimationAgent: LLM MG 降级文本已禁用，动画 [%s] 跳过（不显示描述文字）",
            anim_name,
        )
        self._add_trace_warning(
            f"LLM MG 动画 [{anim_name}] 无法生成，已跳过（不再降级为文字显示）"
        )

    def _add_trace_warning(self, message: str) -> None:
        """添加 trace 警告事件。"""
        try:
            from clipwright.services.trace import add_event as _evt
            _evt(getattr(self, '_pid', ''), "animation", "warning", message)
        except Exception:
            pass

    # ── 过渡动画处理 ──────────────────────────────────────

    # 当前 ffmpeg 支持的 xfade transition 值缓存（None = 探测失败，视为全支持）
    _xfade_supported: list[str] | None = None

    @classmethod
    def _probe_xfade_transitions(cls) -> list[str] | None:
        """B10: 探测 ffmpeg 支持的 xfade transition 值列表（进程内缓存）。

        morph/pixelize/zoomin 等值依赖 ffmpeg 版本（部分需 ≥5.1/6.0），
        原实现无能力探测，不支持的值直到渲染期才 EINVAL。失败返回 None。
        """
        if cls._xfade_supported is not None:
            return cls._xfade_supported
        try:
            import subprocess

            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
                capture_output=True, text=True, timeout=10,
            )
            out = (r.stdout or "") + (r.stderr or "")
            values: list[str] = []
            for line in out.splitlines():
                stripped = line.strip()
                # 形如 "fade           0            ..FV....... fade"
                parts = stripped.split()
                if len(parts) >= 2 and parts[1].isdigit() and parts[0].replace("_", "").isalpha():
                    values.append(parts[0])
            cls._xfade_supported = values if values else None
        except Exception:
            cls._xfade_supported = None
        return cls._xfade_supported

    def _handle_transition_animation(
        self,
        clip: Clip,
        prev_clip: Clip | None,
        anim_id: str,
        anim_name: str,
    ) -> None:
        """为 clip 设置过渡动画属性，供 RenderService 使用 xfade filter。

        AnimationRegistry 中的 transition 动画：
        - crossfade → xfade transition
        - fade_to_black → fade filter
        - push_left/push_right/wipe_left/zoom_in/glitch/pixel_dissolve/slide_up → xfade type
        """
        if prev_clip is None:
            return

        # 从 AnimationRegistry 查找转场持续时间
        anim_def = AnimationRegistry.get(anim_id)
        duration_sec = anim_def.duration_sec if anim_def else 0.4

        # 映射到 FFmpeg xfade transition 类型（C4: 复用公共映射表；
        # 修正 slide_up 方向错误）
        from clipwright.animation.xfade_map import XFADE_MAP
        xfade_map = XFADE_MAP

        mapped = xfade_map.get(anim_id, "fade")
        # 运行时能力探测——当前 ffmpeg 不支持的 transition 值在 Agent 阶段
        # 降级 fade 并发 trace warning（原实现在渲染期 EINVAL 且静默硬切）
        supported = self._probe_xfade_transitions()
        if supported is not None and mapped not in supported:
            self._add_trace_warning(
                f"转场 {anim_id}→{mapped} 当前 ffmpeg 不支持，降级为 fade"
            )
            logger.warning("AnimationAgent: xfade=%s 不支持（转场 %s），降级 fade", mapped, anim_id)
            mapped = "fade"

        clip.transition_in = mapped
        clip.transition_duration_sec = duration_sec
        clip.metadata = clip.metadata or {}
        clip.metadata["transition_id"] = anim_id
        clip.metadata["transition_name"] = anim_name

        logger.info("AnimationAgent: 过渡 %s → clip=%s (type=%s, dur=%.1fs)",
                    anim_name, clip.id[:8], clip.transition_in, duration_sec)

    # ── 轨道管理 ──────────────────────────────────────────

    @staticmethod
    def _find_or_create_track(
        timeline: object,
        kind: ClipKind,
        name: str,
        preferred_index: int,
    ) -> Track:
        for t in timeline.tracks:
            if str(t.kind) == kind.value:
                return t
        existing_indices = {t.index for t in timeline.tracks}
        idx = preferred_index
        while idx in existing_indices:
            idx += 1
        track = Track(id=_uid("t"), name=name, kind=kind, index=idx)
        timeline.tracks.append(track)
        return track

    @staticmethod
    def _ensure_anim_track(timeline: object) -> Track:
        for t in timeline.tracks:
            if str(t.kind) == ClipKind.ANIMATION.value:
                return t
        max_idx = max((t.index for t in timeline.tracks), default=0)
        track = Track(
            id=_uid("t"), name="动画轨",
            kind=ClipKind.ANIMATION, index=max_idx + 1,
        )
        timeline.tracks.append(track)
        return track

    @staticmethod
    async def _hyperframes_available(timeout: float = 120.0) -> bool:
        """异步检测 Hyperframes CLI 是否可用（等待冷启动，最多 timeout 秒）。

        冷启动根因：``is_available()`` 底层 ``_CachedProbe.get_sync()`` 首次调用返回
        default=False（见 services/async_util.py），会让首个 pipeline 把逻辑动画误降级
        为静态 drawtext。这里改用 ``await_available`` 轮询等待真实可用性。
        """
        try:
            from clipwright.animation.hyperframes_renderer import HyperframesRenderer
            return await HyperframesRenderer.await_available(timeout)
        except Exception:
            return False

    # ── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _find_overlapping_clip(track: Track, clip: Clip) -> Clip | None:
        cs = clip.start_sec
        ce = cs + clip.duration_sec
        for tc in track.clips:
            ts = tc.start_sec
            te = ts + tc.duration_sec
            if ts < ce and te > cs:
                return tc
        return None

    @staticmethod
    def _strip_json_payload(text: str) -> str:
        """若 text 是 JSON payload 串（旧 marker 残留），提取其中 text/description。

        非 JSON 或解析失败时原样返回。"""
        if not text or not text.strip().startswith("{"):
            return text
        try:
            import json as _json
            payload = _json.loads(text.strip())
        except Exception:
            return text
        if not isinstance(payload, dict):
            return text
        clean = payload.get("text", "") or payload.get("description", "") or ""
        return clean if clean else text

    @staticmethod
    def _extract_text_content(clip: Clip, marker: dict[str, Any] | None = None) -> str:
        """从 clip 中提取文字内容。

        优先级：
        1. marker 中解析出的 text 字段（[文字动画]打字：xxx → "xxx"）
        2. clip.metadata.text
        3. clip.metadata.label
        4. clip.metadata.source_title
        5. description 中标记后的文字

        仅返回文本；如需区分提取来源（真实旁白 vs 场景描述兜底），
        使用 ``_extract_text_with_source``。
        """
        text, _ = AnimationAgent._extract_text_with_source(clip, marker)
        return text

    @staticmethod
    def _extract_text_with_source(
        clip: Clip, marker: dict[str, Any] | None = None
    ) -> tuple[str, str]:
        """从 clip 中提取文字内容并返回来源标记。

        Returns:
            (text, source)，其中 source ∈ {"marker", "metadata", "description", ""}
            - "marker"     ：marker.text —— 制作计划/用户真实旁白
            - "metadata"   ：metadata.text / label / source_title —— 真实旁白
            - "description"：description 正则兜底 —— 场景描述文本（不可作字幕）
            - ""           ：无内容
        """
        # 1. marker 中解析的文字（真实旁白）
        if marker and marker.get("text"):
            return marker["text"], "marker"

        meta = clip.metadata or {}

        # 2. metadata 直接字段（真实旁白）
        text = meta.get("text", "") or meta.get("label", "") or meta.get("source_title", "")
        if text:
            return text, "metadata"

        # 3. description 中标记后面的文字（场景描述兜底，非真实旁白）
        desc = meta.get("description", "") or ""
        if desc:
            import re
            # 尝试 [文字动画]xxx：text 格式
            for m in re.finditer(
                r'\[(?:文字动画|逻辑动画|动画)\]\S+\s*[：:—\-]\s*(.+?)(?:$|[\n。！？])',
                desc,
            ):
                return m.group(1).strip(), "description"

        return "", ""

    async def _resolve_style(
        self,
        visual_config: dict[str, Any] | None,
        extra_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """从 Persona 配置中解析视觉风格（LLM 驱动 + 插件可覆盖）。"""
        from clipwright.services.style_interpreter import StyleInterpreter

        # 构建 Persona 上下文（含完整 identity/rhythm 等 + vision_prompt）
        ctx = dict(extra_params or {})
        persona_config = ctx.pop("_persona_config", {})
        identity = ctx.pop("_identity", {})
        persona_context = {
            "identity": identity,
            "extra_params": ctx,
            "vision_prompt": getattr(self, "_vision_prompt", "") or "",
            **persona_config,
        }

        config = dict(visual_config or {})
        # 问题3修复：兼容 animation_styles（dict，如 Zam: data_visualization/text_emphasis）
        # → 拼接为 style_description 字符串，注入 StyleInterpreter 与 LLM MG prompt。
        anim_styles = config.get("animation_styles")
        if isinstance(anim_styles, dict) and anim_styles:
            style_desc_parts = []
            for _k, _v in anim_styles.items():
                if isinstance(_v, str) and _v.strip():
                    style_desc_parts.append(_v.strip())
            if style_desc_parts and not config.get("style_description"):
                config["style_description"] = "；".join(style_desc_parts)
        # ── 结构化色板（PRD §16 R4）──
        # 旧实现无条件注入黑白红作为兜底，并把「以 Persona 注入值为最终准绳」写进
        # LLM 上下文 —— 结果任何 persona 的动画都被强制成 Zam 的配色。
        # 现在只做两件事：
        #   1) persona 真的提供了结构化色板才使用；
        #   2) 没提供时**不编造**，交给 palette 文字描述（`_build_context_section`
        #      的「配色描述」字段）+ DiagramStyle 默认值，并标注来源 fallback。
        has_structured_palette = any(
            config.get(k) for k in ("primary_color", "secondary_color", "accent_color")
        )
        config["palette_source"] = "persona" if has_structured_palette else "fallback"
        if not has_structured_palette and config.get("palette"):
            logger.info(
                "AnimationAgent: persona 无结构化色板，使用 palette 描述生成（source=fallback）"
            )
        # 成本优化（可选）：局部剥离 palette 传给 StyleInterpreter——剥离后 `not palette` 为真，
        # has_exact 快路径触发（有精确 primary_color 即跳过 LLM 调用）。仅影响传给 interpret 的
        # 副本，persona_config / yaml 原样保留（material_agent 的 palette 消费点不受影响）。
        fast_cfg = {k: v for k, v in config.items() if k != "palette"}
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        plugin_prompts = PluginPromptRegistry.get_for_agent("animation")
        if plugin_prompts:
            persona_context["_plugin_prompts"] = plugin_prompts
        result = await StyleInterpreter.interpret(fast_cfg, persona_context)
        # 问题3修复：保留 style_description（animation_styles 拼接的动画风格描述），
        # 供下游（LLM MG prompt / 文字动画消费点）使用——StyleInterpreter 只返回结构化字段。
        if config.get("style_description") and isinstance(result, dict):
            result.setdefault("style_description", config["style_description"])
        if isinstance(result, dict):
            # PRD §16 R4：把色板来源带出去（persona / fallback），供 prompt 与 trace 标注
            result.setdefault("palette_source", config.get("palette_source", "fallback"))
            if config.get("palette") and not result.get("palette"):
                result["palette"] = config["palette"]
        return result

    @staticmethod
    def _build_diagram_params(
        anim_id: str, text: str, duration_sec: float
    ) -> dict[str, Any]:
        """根据逻辑动画 ID 构建 text_diagram 参数（存储到 metadata，供 Render 使用）。"""
        base: dict[str, Any] = {
            "duration_sec": duration_sec,
            "title": text[:40],
            "preset": anim_id,
        }

        if anim_id in ("diagram", "causation"):
            items = [t.strip() for t in text.split("→")] if "→" in text else [text[:20]]
            base["items"] = items
            base["relations"] = [
                {"from": i, "to": i + 1, "label": "→"}
                for i in range(len(items) - 1)
            ]
        elif anim_id == "comparison":
            parts = (
                [t.strip() for t in text.replace("vs", " VS ").replace("V S", " VS ").split("VS")]
                if "VS" in text.upper()
                else [text[:15], ""]
            )
            base["items"] = [parts[0] if parts else text[:15], parts[1] if len(parts) > 1 else ""]
            base["relations"] = [
                {"from": 0, "to": 1, "label": "VS", "highlight": 0},
            ]
        elif anim_id == "sequence":
            steps = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "→" in text or "|" in text
                else [text[:15], "步骤2", "步骤3"]
            )
            base["items"] = steps[:8]
            base["relations"] = [
                {"from": i, "to": i + 1, "label": "→"}
                for i in range(len(steps[:8]) - 1)
            ]
        elif anim_id in ("timeline", "tree", "venn"):
            # 时间线/层级/维恩：用 → 或 | 分隔项
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "→" in text or "|" in text
                else [text[:20]]
            )
            base["items"] = items[:8]
        elif anim_id == "hierarchy":
            # 层级图 = tree 的别名，用 → 或 | 分隔
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "→" in text or "|" in text
                else [text[:20]]
            )
            base["items"] = items[:9]
            base["preset"] = "hierarchy"
        elif anim_id in ("bar_chart", "pie_chart", "line_chart"):
            # 数据图表：每项格式 "标签:数值" 或纯标签（自动生成值）
            items = (
                [t.strip() for t in text.replace("|", ",").split(",")]
                if "|" in text or "," in text
                else [text[:20]]
            )
            base["items"] = items[:8]
        # 插件图解类型（由 logic_animations 插件提供）
        elif anim_id == "mindmap":
            # "中心词|子1|子2|子3"
            items = [t.strip() for t in text.split("|")] if "|" in text else [text[:20]]
            base["items"] = items[:13]
        elif anim_id == "radar":
            # "标签1:80,标签2:60,标签3:90"
            items = (
                [t.strip() for t in text.replace("|", ",").split(",")]
                if "," in text or "|" in text
                else [text[:20]]
            )
            base["items"] = items[:8]
        elif anim_id == "gantt":
            # "任务1:0:5,任务2:3:8,任务3:7:4"
            items = (
                [t.strip() for t in text.replace("|", ",").split(",")]
                if "," in text or "|" in text
                else [text[:20]]
            )
            base["items"] = items[:10]
        elif anim_id == "venn3":
            # "A|B|C|AB交|AC交|BC交|ABC交"
            items = [t.strip() for t in text.split("|")] if "|" in text else [text[:20]]
            base["items"] = items[:7]
        elif anim_id == "heatmap":
            # "列1,列2,列3|行1:1,2,3|行2:4,5,6"
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "|" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:8]
        elif anim_id == "sankey":
            # "源:目标:值|源2:目标2:值2"
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "|" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:10]
        elif anim_id == "concept":
            # "节点A:100:200:标签A|节点B:400:200:标签B|节点A->节点B:关系标签"
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "|" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:12]
        elif anim_id == "codeblock":
            # codeblock: 用 → 分隔代码行（每行为一行代码）
            items = (
                [t.strip() for t in text.replace("→", "\n").split("\n")]
                if "\n" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:20]
        elif anim_id == "datatable":
            # "表头1,表头2,表头3|行1A,行1B,行1C|行2A,行2B,行2C"
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "|" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:8]
        elif anim_id == "quote":
            # "引用内容|作者名"
            items = [t.strip() for t in text.split("|")] if "|" in text else [text[:20]]
            base["items"] = items[:2]
        elif anim_id == "compcard":
            # "特征,A值,B值,胜出方(1/2)|特征2,A2,B2,胜出方"
            items = (
                [t.strip() for t in text.replace("→", "|").split("|")]
                if "|" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items[:10]
        elif anim_id == "orgchart":
            # "CEO|  VP1|  VP2|   经理A|   经理B"（保留缩进以表示层级）
            items_raw = (
                text.replace("→", "\n").split("\n")
                if "\n" in text or "→" in text
                else [text[:20]]
            )
            base["items"] = items_raw[:15]
        elif anim_id == "flow_chart":
            # 流程图：用 ; 分隔节点，用 -> 分隔边。格式: id:x:y:label:shape;id2...|from->to:label
            parts = text.replace("→", "|").split("|") if "|" in text or "→" in text else [text[:40]]
            base["items"] = parts[:3]
            base["title"] = text[:40]
            base["preset"] = "flow_chart"
        elif anim_id == "sequence_diagram":
            # 序列图：参与者用 , 分隔，消息用 | 分隔。格式: A,B|A->B:消息1|B->A:消息2
            parts = text.replace("→", "|").split("|") if "|" in text or "→" in text else [text[:40]]
            base["items"] = parts[:8]
            base["title"] = text[:40]
            base["preset"] = "sequence_diagram"
        else:
            base["items"] = [text[:20]]

        return base

