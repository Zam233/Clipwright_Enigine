"""动画 Agent（AnimationAgent）— 将动画标记转为时间线修改。

两种动画类型：
1. 文字动画 [文字动画]xxx — 作用于文字轨 clip 的入场 keyframes + 出场淡出
2. 逻辑动画 [逻辑动画]xxx — 独立动画轨，展示逻辑关系（箭头/对比/流程/因果）

动画类型从 AnimationCatalog 动态读取，支持插件扩展。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.animation.catalog import AnimationCatalog
from clipwright.config import logger
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AnimationInput,
    AnimationOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.services.trace import add_event


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class AnimationAgent(BaseAgent[AnimationInput, AnimationOutput]):
    """动画 Agent：解析 [文字动画] / [逻辑动画] 标记 → 修改时间线。

    文字动画：在文字轨创建/更新 text clip，添加完整 keyframes（入场+保持+出场）。
    逻辑动画：在动画轨创建独立 clip，将 diagram 参数存入 metadata 供 Render 使用。
    """

    agent_name = "animation_agent"

    def __init__(self) -> None:
        super().__init__()
        self._catalog = AnimationCatalog()

    async def execute(
        self, input_data: AnimationInput, context: AgentContext
    ) -> AnimationOutput:
        logger.info("AnimationAgent 开始 pipeline=%s", context.pipeline_id[:12])
        add_event(context.pipeline_id, "animation", "agent_start",
                  "AnimationAgent 开始（文字动画/逻辑动画分流）")

        try:
            timeline = input_data.timeline
            if timeline is None or not timeline.tracks:
                logger.info("AnimationAgent: 时间线为空，跳过")
                return AnimationOutput(decision=AgentDecision.PASS, timeline=timeline)

            # 解析视觉风格
            persona_style = self._resolve_style(
                input_data.visual_config, context.extra_params
            )

            text_track = self._find_or_create_track(timeline, ClipKind.TEXT, "文字轨", 1)
            anim_track = None  # 延迟创建

            text_anim_count = 0
            logic_anim_count = 0

            # 遍历所有 video/image 轨 clip，检测标记
            for vid_track in timeline.tracks:
                if str(vid_track.kind) not in ("video", "image"):
                    continue

                for clip in list(vid_track.clips or []):
                    meta = clip.metadata or {}
                    desc = meta.get("description", "") or ""

                    markers = AnimationCatalog.parse_marker_from_description(desc)
                    if not markers:
                        continue

                    marker = markers[0]  # 一个场景只处理一个动画标记
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
                        await self._handle_logic_animation(
                            anim_track, clip, anim_id, anim_name, marker,
                        )
                        logic_anim_count += 1
                        logger.info("AnimationAgent: [逻辑动画]%s → %s (id=%s)",
                                    anim_name, clip.id[:8], anim_id)
                        add_event(context.pipeline_id, "animation", "logic_anim",
                                  f"[逻辑动画]{anim_name}({anim_id}) → clip={clip.id[:8]}")

            summary = (
                f"AnimationAgent: 文字动画={text_anim_count}, 逻辑动画={logic_anim_count}"
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
        clip_duration = max(vid_clip.duration_sec, 1.0)
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

        text_clip = Clip(
            id=_uid("tc"),
            kind=ClipKind.TEXT,
            asset_id="",
            track_id=text_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=text_content or marker.get("text", ""),
            font="sans-serif",
            font_size=persona_style.get("font_size", 48),
            font_color=persona_style.get("font_color", "#ffffff"),
            keyframes=full_kfs,
            metadata={
                "anim_type": anim_id,
                "anim_name": anim_name,
                "renderer": "drawtext",
                "position": persona_style.get("position", "bottom"),
                "stroke_width": persona_style.get("stroke_width", 1),
                "stroke_color": persona_style.get("stroke_color", "#000000"),
            },
        )
        if anim_id in ("typewriter", "char_by_char"):
            text_clip.metadata["typewriter"] = True

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
        clip_duration = max(vid_clip.duration_sec, 1.0)
        caption_clip = Clip(
            id=_uid("cc"),
            kind=ClipKind.CAPTION,
            asset_id="",
            track_id=text_track.id,
            start_sec=vid_clip.start_sec,
            duration_sec=clip_duration,
            text=text_content,
            font="sans-serif",
            font_size=persona_style.get("font_size", 36),
            font_color=persona_style.get("font_color", "#ffffff"),
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
    ) -> None:
        """在动画轨创建独立的逻辑动画 clip，由 Hyperframes 渲染 SVG。"""
        text_content = marker.get("text", self._extract_text_content(vid_clip, marker))
        if not text_content:
            text_content = "逻辑关系"
        duration = min(vid_clip.duration_sec, 6.0)

        diagram_params = self._build_diagram_params(
            anim_id, text_content, duration
        )

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
                "renderer": "hyperframes",
                "font_size": 48,
                "font_color": "#ffffff",
                "position": "center",
            },
        )
        anim_track.clips.append(anim_clip)
        anim_track.clips.sort(key=lambda c: c.start_sec)

    # ── 轨道管理 ──────────────────────────────────────────

    @staticmethod
    def _find_or_create_track(
        timeline: object,
        kind: ClipKind,
        name: str,
        preferred_index: int,
    ) -> Track:
        for t in timeline.tracks:
            if str(t.kind) == str(kind):
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
            if str(t.kind) == str(ClipKind.ANIMATION):
                return t
        max_idx = max((t.index for t in timeline.tracks), default=0)
        track = Track(
            id=_uid("t"), name="动画轨",
            kind=ClipKind.ANIMATION, index=max_idx + 1,
        )
        timeline.tracks.append(track)
        return track

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

    @staticmethod
    def _resolve_style(
        visual_config: dict[str, Any] | None,
        extra_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """从 Persona 配置中解析文字视觉风格。"""
        return AnimationCatalog.resolve_persona_style(visual_config, extra_params)

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
            parts = [t.strip() for t in text.replace("vs", " VS ").replace("V S", " VS ").split("VS")] if "VS" in text.upper() else [text[:15], ""]
            base["items"] = [parts[0] if parts else text[:15], parts[1] if len(parts) > 1 else ""]
            base["relations"] = [
                {"from": 0, "to": 1, "label": "VS", "highlight": 0},
            ]
        elif anim_id == "sequence":
            steps = [t.strip() for t in text.replace("→", "|").split("|")] if "→" in text or "|" in text else [text[:15], "步骤2", "步骤3"]
            base["items"] = steps[:5]
            base["relations"] = [
                {"from": i, "to": i + 1, "label": "→"}
                for i in range(len(steps[:5]) - 1)
            ]
        else:
            base["items"] = [text[:20]]

        return base
