"""动画 Agent（AnimationAgent）— 将动画标记转为时间线修改。

两种动画类型：
1. 文字动画 [文字动画]xxx — 作用于文字轨 clip 的入场 keyframes + 出场淡出
2. 逻辑动画 [逻辑动画]xxx — 独立动画轨，展示逻辑关系（箭头/对比/流程/因果）

动画类型从 AnimationCatalog 动态读取，支持插件扩展。
"""

from __future__ import annotations

import asyncio
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
from clipwright.services.trace import add_event
from clipwright.services.web_search import WebSearchService

# W5: 数据/事实型 mg_dynamic 检测 — 命中则触发联网搜索，注入事实上下文
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

    def __init__(self) -> None:
        super().__init__()
        self._catalog = AnimationCatalog()
        self._mg_category_context: dict = {}  # 视频类型（category）特征数据
        # 时间线分辨率/帧率（execute() 中从 Timeline 读取，供 MG 渲染器动态解析）
        self._tl_width = 1920
        self._tl_height = 1080
        self._tl_fps = 30.0
        # A7: 图片素材库语义索引 {path, tags, description} — execute() 中构建，
        # 供 mg_dynamic 生成 prompt 让 LLM 主动选择语义匹配图片（防 📡→石墨烯 类错配）
        self._image_index: list[dict[str, Any]] = []

    async def execute(
        self, input_data: AnimationInput, context: AgentContext
    ) -> AnimationOutput:
        logger.info("AnimationAgent 开始 pipeline=%s", context.pipeline_id[:12])
        self._pid = context.pipeline_id
        add_event(context.pipeline_id, "animation", "agent_start",
                  "AnimationAgent 开始（文字动画/逻辑动画分流）")

        # 捕获 Persona 的 prompt / 视觉需求 prompt，供 MG 处理器与风格解析使用
        self._persona_prompt = getattr(input_data, "persona_prompt", None) or ""
        self._vision_prompt = getattr(input_data, "vision_prompt", None) or ""

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

            # A7: 图片素材库语义索引 — 对 image_assets 逐个调用视觉分析器
            # （VisionService.analyze_image）构建 {path, tags, description}；
            # 分析失败回退文件名标签；无 image_assets 时保持原有行为（不调用分析器）。
            self._image_index = []
            try:
                image_assets = getattr(input_data, "image_assets", None) or []
                if image_assets:
                    self._image_index = await self._build_image_semantic_index(image_assets)
                    if self._image_index:
                        logger.info("AnimationAgent: 图片语义索引 %d 项", len(self._image_index))
                        add_event(context.pipeline_id, "animation", "image_index",
                                  f"图片语义索引 {len(self._image_index)} 项（LLM 选图入动画）")
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

                    # B11: 遍历全部标记（原实现只取 markers[0]，多标记场景
                    # 其余动画意图静默丢失）
                    for marker in markers:
                        marker_type = marker.get("type", "text")
                        anim_id = marker.get("anim_id", "text_fade_in")
                        anim_name = marker.get("name", "淡入")

                        if marker_type == "text":
                            self._handle_text_animation(
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

            # 并发执行逻辑动画作业（有界并发，默认 3；clips 写入后按 start_sec 排序保证 z-order）
            if logic_jobs:
                sem = asyncio.Semaphore(max(1, int(getattr(settings, "pipeline_concurrency", 3))))

                async def _run_logic(job):
                    async with sem:
                        track, c, aid, aname, m, style = job
                        add_event(
                            context.pipeline_id, "animation", "mg_start",
                            f"动画生成开始: {aname} → {c.id[:8]}",
                            {"anim_id": aid, "clip_id": c.id},
                        )
                        try:
                            await self._handle_logic_animation(
                                track, c, aid, aname, m, style,
                            )
                            add_event(
                                context.pipeline_id, "animation", "mg_end",
                                f"动画生成完成: {aname} → {c.id[:8]}",
                                {"anim_id": aid, "clip_id": c.id},
                            )
                        except Exception as e:
                            logger.exception("AnimationAgent: 逻辑动画 %s 异常: %s", aname, e)
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

    def _handle_text_animation(
        self,
        text_track: Track,
        vid_clip: Clip,
        anim_id: str,
        anim_name: str,
        marker: dict[str, Any],
        persona_style: dict[str, Any],
    ) -> None:
        """为文字轨创建 text clip。

        文字动画统一走 FFmpeg drawtext（快、无依赖），
        因为所有文字动画类型（fade/slide/typewriter/scale等）
        drawtext 都有实现方案（scale→fontsize 表达式）。
        """
        # B11: 时长钳制——原 max(dur, 1.0) 让 0.2s 片段挂 1s 文字覆盖侵入下一
        # 场景；改为不越出宿主片段，下限 0.2s
        clip_duration = max(0.2, float(vid_clip.duration_sec or 0.2))
        text_content = self._extract_text_content(vid_clip, marker) or marker.get("text", "")

        # 长文本 + typewriter → 路由到字幕轨（CAPTION）
        is_long_text = len(text_content) > 50
        is_typewriter = anim_id in ("typewriter", "char_by_char")
        if is_long_text and is_typewriter:
            self._handle_caption(
                text_track, vid_clip, text_content, persona_style,
            )
            return

        # 生成 FFmpeg keyframes 供 drawtext 渲染
        full_kfs = AnimationCatalog.build_full_keyframes(
            anim_id, vid_clip.start_sec, clip_duration
        )

        # 完整文字样式字段（统一默认 + persona_style 覆盖，任务 32）
        style = _merge_style_defaults(DEFAULT_TEXT_STYLE, persona_style)

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
                "position": persona_style.get("position", "bottom"),
                "stroke_width": persona_style.get("stroke_width", 0),
                "stroke_color": persona_style.get("stroke_color", "#000000"),
            },
        )
        if anim_id in ("typewriter", "char_by_char"):
            text_clip.metadata["typewriter"] = True

        # B11: 启用重叠去重（_find_overlapping_clip 此前定义未调用）——
        # 同轨道文字片段时间重叠会叠字渲染
        overlap = self._find_overlapping_clip(text_track, text_clip)
        if overlap is not None:
            logger.info("AnimationAgent: 跳过重叠文字动画 %s（与 %s 时间重叠）",
                        text_clip.id[:8], overlap.id[:8])
            return

        text_track.clips.append(text_clip)
        text_track.clips.sort(key=lambda c: c.start_sec)

    # ── 长文本字幕处理 ─────────────────────────────────────

    @staticmethod
    def _handle_caption(
        text_track: Track,
        vid_clip: Clip,
        text_content: str,
        persona_style: dict[str, Any],
    ) -> None:
        """长文本 → CAPTION clip，使用 FFmpeg drawtext 渲染。"""
        # B11: 与文字动画同样钳制，不越出宿主片段
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

        # ── LLM 动态 MG 动画路由 ──
        if anim_id == "mg_dynamic":
            await self._handle_llm_mg(
                anim_track, vid_clip, anim_id, anim_name,
                text_content, duration, marker, persona_style,
            )
            return

        # ── MG 动画路由 ──
        if anim_id.startswith("mg_"):
            await self._handle_mg_animation(
                anim_track, vid_clip, anim_id, anim_name,
                text_content, duration, marker, persona_style,
            )
            return

        diagram_params = self._build_diagram_params(
            anim_id, text_content, duration
        )

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
        # P2: 检测 Hyperframes 是否可用，不可用时降级到 drawtext
        hf_available = self._hyperframes_available()
        renderer = "hyperframes" if hf_available else "drawtext"
        if not hf_available:
            logger.info(
                "AnimationAgent: Hyperframes 不可用，逻辑动画 [%s] 降级到 drawtext",
                anim_name,
            )
            # 通过 trace 事件推送用户可见警告
            try:
                from clipwright.services.trace import add_event as _evt
                _evt(getattr(self, '_pid', ''), "animation", "warning",
                     f"Hyperframes 不可用，[逻辑动画]{anim_name} 降级为文字显示",
                     {"anim_id": anim_id, "degradation": "hyperframes_not_available"})
            except Exception:
                pass
            text_clip = Clip(
                id=_uid("lc"),
                kind=ClipKind.TEXT,
                asset_id="",
                track_id=anim_track.id,
                start_sec=vid_clip.start_sec,
                duration_sec=duration,
                text=f"{anim_name}: {text_content[:50]}",
                font_size=diagram_style.get("font_size", 36),
                font_color=diagram_style.get("text_color", "#ffffff"),
                metadata={
                    "anim_type": anim_id,
                    "anim_name": anim_name,
                    "category": "logic",
                    "renderer": "drawtext",
                    "position": "center",
                },
            )
            anim_track.clips.append(text_clip)
            anim_track.clips.sort(key=lambda c: c.start_sec)
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
            logger.warning("MG 动画未找到: %s，降级为文字", anim_id)
            text_clip = Clip(
                id=_uid("lc"), kind=ClipKind.TEXT, asset_id="",
                track_id=anim_track.id,
                start_sec=vid_clip.start_sec, duration_sec=duration,
                text=f"{anim_name}: {text_content[:50]}",
                metadata={"anim_type": anim_id, "renderer": "drawtext"},
            )
            anim_track.clips.append(text_clip)
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

        mg_dur = mg_def.get("duration_sec", duration)
        clip_dur = min(mg_dur, vid_clip.duration_sec)

        # 渲染为 HTML（时间线尺寸优先，动态解析分辨率/帧率）
        try:
            html = MGRenderer.render(
                mg_def, mg_params,
                width=self._tl_width, height=self._tl_height, fps=self._tl_fps,
            )
        except Exception as e:
            logger.warning("MG 动画渲染失败: %s", e)
            text_clip = Clip(
                id=_uid("lc"), kind=ClipKind.TEXT, asset_id="",
                track_id=anim_track.id,
                start_sec=vid_clip.start_sec, duration_sec=clip_dur,
                text=f"{anim_name}: {text_content[:50]}",
                metadata={"anim_type": anim_id, "renderer": "drawtext"},
            )
            anim_track.clips.append(text_clip)
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
                "mg_html_path": "",
                "position": "center",
            },
        )
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
        try:
            from clipwright.animation.mg import MGGenerator
            mg_gen = MGGenerator()
        except Exception as e:
            logger.warning("AnimationAgent: llm_mg 引擎初始化失败: %s", e)
            mg_gen = None

        if mg_gen is None:
            logger.warning("AnimationAgent: llm_mg 引擎不可用，mg_dynamic 降级为 drawtext")
            self._add_trace_warning("LLM MG 引擎不可用，动画降级为文字显示")
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

        # A7: 图片语义索引注入 mg_dynamic 生成 prompt — 让 LLM 理解图片内容后
        # 主动选择语义匹配的图片放入动画 image 元素（防图标语义错配）
        gen_description = description
        if getattr(self, "_image_index", None):
            gen_description = description + self._format_image_index_prompt(self._image_index)

        # W5: 数据/事实型 mg_dynamic → 联网搜索，注入事实上下文
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
            result = await mg_gen.generate(
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
                "mg_method": method,
                "mg_generation_id": generation_id,
                "mg_fallback_template": result.get("fallback_template"),
                "position": "center",
            },
        )
        anim_track.clips.append(anim_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)
        logger.info("AnimationAgent: [LLM MG]%s → method=%s, html=%d chars",
                     anim_name, method, len(html))
        self._llm_mg_generated += 1

    # ── A7: 图片素材库语义索引 ────────────────────────────────

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
        """创建降级文字 clip。"""
        text_clip = Clip(
            id=_uid("fl"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id=anim_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=min(duration, 5.0),
            text=f"{anim_name}: {text_content[:50]}",
            font_size=36,
            font_color="#ffffff",
            metadata={
                "anim_type": "fallback_text",
                "renderer": "drawtext",
                "position": "center",
            },
        )
        anim_track.clips.append(text_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)

    def _add_trace_warning(self, message: str) -> None:
        """添加 trace 警告事件。"""
        try:
            from clipwright.services.trace import add_event as _evt
            _evt(getattr(self, '_pid', ''), "animation", "warning", message)
        except Exception:
            pass

    # ── 过渡动画处理 ──────────────────────────────────────

    # B10: 当前 ffmpeg 支持的 xfade transition 值缓存（None = 探测失败，视为全支持）
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
        # B10: 修正 slide_up 方向错误）
        from clipwright.animation.xfade_map import XFADE_MAP
        xfade_map = XFADE_MAP

        mapped = xfade_map.get(anim_id, "fade")
        # B10: 运行时能力探测——当前 ffmpeg 不支持的 transition 值在 Agent 阶段
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
    def _hyperframes_available() -> bool:
        """检测 Hyperframes CLI 是否可用。"""
        try:
            from clipwright.animation.hyperframes_renderer import HyperframesRenderer
            return HyperframesRenderer.is_available()
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
        """
        # 1. marker 中解析的文字
        if marker and marker.get("text"):
            return marker["text"]

        meta = clip.metadata or {}

        # 2. metadata 直接字段
        text = meta.get("text", "") or meta.get("label", "") or meta.get("source_title", "")
        if text:
            return text

        # 3. description 中标记后面的文字
        desc = meta.get("description", "") or ""
        if desc:
            import re
            # 尝试 [文字动画]xxx：text 格式
            for m in re.finditer(
                r'\[(?:文字动画|逻辑动画|动画)\]\S+\s*[：:—\-]\s*(.+?)(?:$|[\n。！？])',
                desc,
            ):
                return m.group(1).strip()

        return ""

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
        # Q2 主修复：原始层注入兜底色板（黑白红）。
        # persona 参数层 primary/secondary/accent 为 null 时（如 Zam），注入非空结构化字段，
        # 使 _apply_structured_overrides 无论走 LLM/tone-fallback/快路径都把结果盖回黑白红——
        # 注入是正确性保证（不依赖 LLM 恰好生成对色板）。
        if not config.get("primary_color"):
            config["primary_color"] = "#000000"
        if not config.get("secondary_color"):
            config["secondary_color"] = "#FFFFFF"
        if not config.get("accent_color"):
            config["accent_color"] = "#FF0000"
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
