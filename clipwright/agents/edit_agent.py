"""剪辑 Agent（EditAgent）— 时间线生成。

核心职责：
1. 接收 MaterialAgent 的候选素材 + StructureAgent 的脚本骨架
2. 从 category_plugin.translate_persona() 获取节奏参数
3. 为每个场景选取最佳素材 → 裁剪 → 放置到时间线
4. 输出粗剪 Timeline 供 AnimationAgent 加工
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.config import logger, settings
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    EditInput,
    EditOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, ImageFit, Timeline, Track
from clipwright.services.llm import LLMService
from clipwright.services.trace import add_event
from clipwright.tool.registry import ToolRegistry

# 素材裁剪缓存：同一 (源路径, 起点, 时长) 的裁剪结果复用，避免对同一网络素材重复下载/裁剪。
# 有界（最多 _TRIM_CACHE_MAX 条），超出时清空重建。
_TRIM_CACHE: dict[tuple, str] = {}
_TRIM_CACHE_MAX = 512


def _split_sentences(text: str) -> list[str]:
    """按中文标点切分口播文案为句子（字幕粒度）。

    与前端 HomePage 的按标点切分规则一致（'，。！；？：' 为边界）；
    '？！' 连标点保留在前句，其余标点作为边界消费。
    结果去空。长句（>40 字）二次按逗号切分，保证字幕可读。
    """
    t = (text or "").strip()
    if not t:
        return []
    parts: list[str] = []
    buf = ""
    for ch in t:
        if ch in "，。！；？：":
            if ch in "！？":
                parts.append((buf + ch).strip())
            elif buf.strip():
                parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if len(p) > 40:
            # 长句按逗号二次切分，保留标点
            sub = ""
            for ch in p:
                sub += ch
                if ch in "，、；" and len(sub) >= 10:
                    out.append(sub.strip())
                    sub = ""
            if sub.strip():
                out.append(sub.strip())
        else:
            out.append(p)
    return [p for p in out if p]


def _append_caption_sentences(
    caption_track: "Track",
    sentences: list[str],
    start_sec: float,
    total_dur: float,
) -> None:
    """把一组句子分配进 [start_sec, start_sec+total_dur]，追加为 CAPTION clip。

    配音驱动对齐：场景总长已按配音时长缩放，句子按字数占比分配时间 →
    字幕与配音时间轴一致。clip.kind=CAPTION、renderer=ass（走 ASS/libass 渲染）。

    时长分配保证 Σdur == total_dur 精确、相邻 clip 无重叠（含跨场景）：
    1. 退化预检：非末句有 0.3s 最小时长保护，若 (n-1)*0.3 超出 total_dur*0.85，
       把尾部句子文本拼进前一个 clip（合并），直到 (merged-1)*0.3 <= total_dur*0.85，
       使 min 钳制不再超界（修复叠字：累积超时导致前句吞掉下场景时间）；
    2. 非末句按字数比例分配（min 0.3s 保护），末句吸收剩余
       max(0.05, total_dur - (t_cursor - start_sec))，保证 Σ == total_dur 且末句不越界。
    字幕样式用显式非零 kwargs 注入（stroke/shadow），render 读 Clip 属性 → ASS
    \\bord\\shad 生效——不依赖 DEFAULT_CAPTION_STYLE（全 0 无效）。
    """
    if not sentences or total_dur <= 0:
        return
    # 退化预检：过多短句时 min 钳制会超界 → 合并尾部句子直到 (n-1)*0.3 <= total_dur*0.85
    merged = list(sentences)
    while len(merged) > 1 and (len(merged) - 1) * 0.3 > total_dur * 0.85:
        merged[-2] = f"{merged[-2]}{merged[-1]}"
        merged.pop()
    seg_lens = [max(len(s), 1) for s in merged]
    total_len = sum(seg_lens)
    t_cursor = start_sec
    for si, sent in enumerate(merged):
        if si == len(merged) - 1:
            # 末句吸收剩余：保证 Σ == total_dur 精确，且不越界到下场景
            s_dur = max(0.05, total_dur - (t_cursor - start_sec))
        else:
            ratio = seg_lens[si] / total_len
            s_dur = max(0.3, ratio * total_dur)
        caption_clip = Clip(
            id=_uid("cc"),
            kind=ClipKind.CAPTION,
            asset_id="",
            track_id=caption_track.id,
            start_sec=round(t_cursor, 3),
            duration_sec=round(s_dur, 3),
            text=sent,
            font="sans-serif",
            stroke_width=2.0,
            stroke_color="#000000",
            shadow_x=1.0,
            shadow_y=1.0,
            shadow_color="#80000000",
            metadata={
                "category": "caption",
                "position": "bottom",
                "renderer": "ass",
            },
        )
        caption_track.clips.append(caption_clip)
        t_cursor += s_dur
    caption_track.clips.sort(key=lambda c: c.start_sec)


def _trim_cache_get(source_path: str, start_sec: float, duration_sec: float) -> str | None:
    return _TRIM_CACHE.get((source_path, round(start_sec, 2), round(duration_sec, 2)))


def _trim_cache_set(source_path: str, start_sec: float, duration_sec: float, output_path: str) -> None:
    if len(_TRIM_CACHE) >= _TRIM_CACHE_MAX:
        _TRIM_CACHE.clear()
    _TRIM_CACHE[(source_path, round(start_sec, 2), round(duration_sec, 2))] = output_path


class EditAgent(BaseAgent[EditInput, EditOutput]):
    """剪辑 Agent：从脚本骨架和素材生成粗剪时间线。"""

    agent_name = "edit_agent"

    def __init__(self) -> None:
        super().__init__()
        self._llm = LLMService()

    async def execute(
        self, input_data: EditInput, context: AgentContext
    ) -> EditOutput:
        notes: list[str] = []
        try:
            # 1. 解析输入
            scenes = input_data.script_skeleton.get("scenes", [])
            candidate_clips = input_data.candidate_clips or []
            # 全局口播文案（配音驱动字幕的 fallback 源）：extra_params.script_text（requirements 完整文案）
            # → script_skeleton.voiceover → creative_brief 文案 → 场景拼接
            _sk = input_data.script_skeleton or {}
            _brief = input_data.creative_brief or {}
            _brief_draft = _brief.get("brief_draft") or _brief.get("creative_brief") or {}
            global_voice = (
                (context.extra_params or {}).get("script_text")
                or _sk.get("voiceover")
                or _sk.get("script")
                or _brief_draft.get("overview")
                or _brief_draft.get("script")
                or _brief_draft.get("content")
                or ""
            ) or ""
            if not global_voice:
                # 最后兜底：场景 description/voiceover 拼接
                global_voice = "。".join(
                    str(s.get("voiceover_script") or s.get("text") or "").strip()
                    for s in scenes if (s.get("voiceover_script") or s.get("text"))
                )
            logger.info("EditAgent: 全局文案 %d 字（字幕 fallback 源）", len(global_voice))
            logger.info("EditAgent: %d 个场景, %d 候选素材", len(scenes), len(candidate_clips))
            if not scenes:
                logger.warning("EditAgent: 场景列表为空，无法生成时间线")
                return EditOutput(
                    decision=AgentDecision.FAIL,
                    timeline=Timeline(id=_uid("tl"), width=1920, height=1080, fps=30),
                    edit_notes=["场景列表为空"],
                )

            # 2. 获取 Persona 剪辑参数（经 category_plugin 翻译）
            shot_params = context.extra_params.get("shot_params", {})
            cut_profile = context.extra_params.get("cut_profile", "even_flow")
            transition_weights = context.extra_params.get("transition_weights", {})
            # P8: 节拍对齐剪辑 beat-sync — cut_on_beat + BPM（来自类型配置/音频检测）
            cut_on_beat = bool(context.extra_params.get("cut_on_beat", False))
            beat_bpm = float(context.extra_params.get("bpm", 120) or 120)

            base_shot_ms = shot_params.get("base_shot_ms", 5000)

            # 2.5 LLM 剪辑档案决策（A1）：内容情绪 + persona cut_profile + 类型节奏 → 结构化 JSON。
            # 任何失败（LLM 禁用/异常/非 JSON/字段非法）→ None → 沿用下方现有规则，
            # 管线在 LLM 不可用时仍可正常运行（Must-Not-Have：保留规则回退）。
            llm_profile = await self._llm_decide_edit_profile(
                scenes,
                {
                    "persona_id": context.persona_id,
                    "topic": context.topic,
                    "cut_profile": cut_profile,
                    "shot_params": shot_params,
                    "transition_weights": transition_weights,
                },
                context.category_plugin_id,
                pipeline_id=context.pipeline_id,
            )
            llm_pip_scenes: set[int] = set()
            if llm_profile is not None:
                base_shot_ms = llm_profile["base_shot_ms"]
                transition_weights = llm_profile["transition_weights"]
                llm_pip_scenes = set(llm_profile["pip_scenes"])
                notes.append(
                    f"LLM 剪辑档案: base_shot_ms={base_shot_ms}ms, "
                    f"transitions={','.join(sorted(transition_weights.keys()))}, "
                    f"pip_scenes={sorted(llm_pip_scenes)}"
                )
                notes.extend(llm_profile["pacing_notes"])
            else:
                notes.append("LLM 剪辑档案不可用，沿用规则剪辑参数")

            base_shot_sec = base_shot_ms / 1000.0
            pref_transition = max(
                transition_weights,
                key=transition_weights.get,
            ) if transition_weights else "hard_cut"

            # 3. 构建轨道（检测是否需要 PiP 画中画叠加轨）
            vid_track = Track(id=_uid("t"), name="视频轨", kind=ClipKind.VIDEO, index=0)
            text_track = Track(id=_uid("t"), name="文字轨", kind=ClipKind.TEXT, index=1)
            caption_track = Track(id=_uid("t"), name="字幕轨", kind=ClipKind.CAPTION, index=2)
            audio_track = Track(id=_uid("t"), name="音频轨", kind=ClipKind.AUDIO, index=3)
            pip_track = None  # 画中画轨，按需创建

            # 检查场景描述中是否有 PiP 需求；LLM 档案指定的 pip_scenes 同样触发
            has_pip = any(
                "画中画" in (s.get("description", "") or "")
                or "叠加" in (s.get("description", "") or "")
                or "PiP" in (s.get("description", "") or "")
                for s in scenes
            ) or bool(llm_pip_scenes)
            if has_pip:
                pip_track = Track(id=_uid("t"), name="画中画", kind=ClipKind.VIDEO, index=4)

            # 4. 标准化场景时长：优先规划书总时长 → 音频总时长 → 场景和
            scene_count = len(scenes)
            total_scene_duration = sum(
                s.get("duration_sec", base_shot_sec) for s in scenes
            )
            target_duration = context.extra_params.get(
                "audio_duration_sec", total_scene_duration
            )
            try:
                plan = input_data.production_plan or {}
                plan_dur = plan.get("total_duration_sec")
                if isinstance(plan_dur, (int, float)) and plan_dur > 0:
                    target_duration = float(plan_dur)
                    notes.append(f"按规划书总时长对齐: {target_duration:.0f}s")
            except Exception:
                pass
            logger.info("EditAgent 时长: scenes=%d, sum=%.0fs, target=%.0fs (extra audio_duration_sec=%s)",
                        scene_count, total_scene_duration, target_duration,
                        context.extra_params.get("audio_duration_sec"))
            if target_duration > 0 and total_scene_duration > 0:
                duration_scale = target_duration / total_scene_duration
                notes.append(f"时长缩放: {total_scene_duration:.0f}s → {target_duration:.0f}s (x{duration_scale:.2f})")
            else:
                duration_scale = 1.0
                notes.append(f"无时长缩放: total={total_scene_duration:.0f}s, target={target_duration:.0f}s")

            # 5. 对每个场景取素材（多段素材拼接，填满场景时长）
            # 场景裁剪/文字占位是主要 IO（网络下载 + ffmpeg）→ 场景级有界并行。
            # 关键：场景处理只计算本地放置段（不触碰共享轨道 / current_time），
            # 轨道装配在下方按场景顺序串行执行，保证时间线顺序与串行版完全一致。
            scene_concurrency = max(1, int(getattr(settings, "material_concurrency", 6)))
            scene_sem = asyncio.Semaphore(scene_concurrency)

            async def _process_one(i: int, scene: dict[str, Any]) -> list[dict[str, Any]]:
                async with scene_sem:
                    scene_duration = scene.get("duration_sec", base_shot_sec) * duration_scale
                    return await self._process_scene_units(
                        i, scene,
                        candidate_clips=candidate_clips,
                        scene_duration=scene_duration,
                        context=context,
                        pip_scene_indices=llm_pip_scenes,
                    )

            scene_results = await asyncio.gather(
                *(_process_one(i, s) for i, s in enumerate(scenes))
            )

            # 串行装配：clip 放置在共享轨道上，start_sec 由全局 current_time 累积决定，
            # 必须按场景顺序逐个放置，才能复现串行版的 clip 顺序 / start_sec / scene_asset_map 键。
            current_time = 0.0
            scene_asset_map: dict[str, dict] = {}
            missing_scenes: list[tuple[float, float, int]] = []  # (start, dur, scene_idx) 缺字幕的场景

            # P8: beat-sync 场景起点吸附函数（BPM → 拍间隔）
            beat_interval = 60.0 / max(beat_bpm, 30.0) if cut_on_beat else None

            for i, units in enumerate(scene_results):
                scene_desc = scenes[i].get("description", "") if i < len(scenes) else ""
                # 场景起点吸附到最近拍点（非首场景）
                if beat_interval is not None and i > 0:
                    current_time = round(current_time / beat_interval) * beat_interval
                scene_start_time = current_time
                scene_total_dur = sum(u["seg_dur"] for u in units)
                for ui, unit in enumerate(units):
                    seg_dur = unit["seg_dur"]

                    # 添加视频 clip（注入场景描述供 Animation Agent 检测 [文字动画]/[逻辑动画] 标记）
                    vid_clip = self._make_clip(
                        kind=ClipKind.VIDEO,
                        track_id=vid_track.id,
                        start_sec=current_time,
                        duration_sec=seg_dur,
                        asset=unit["asset"],
                        clip_label=f"v_{i}_{len(vid_track.clips)}",
                        processed_path=unit["processed_path"],
                    )
                    # LLM 剪辑档案转场注入：场景边界（非首场景的首个 clip）使用 LLM 首选转场，
                    # hard_cut 为默认无转场不注入；仅 LLM 档案生效路径触发（回退路径保持原样）。
                    if (
                        llm_profile is not None
                        and pref_transition != "hard_cut"
                        and ui == 0
                        and i > 0
                    ):
                        vid_clip.transition_in = pref_transition
                        vid_clip.transition_duration_sec = 0.4
                    # 注入描述信息到 metadata
                    if any(m in scene_desc for m in ["[动画]", "[转场]", "[文字动画]", "[逻辑动画]"]):
                        if not vid_clip.metadata:
                            vid_clip.metadata = {}
                        vid_clip.metadata["description"] = scene_desc
                    vid_track.clips.append(vid_clip)

                    # 画中画：场景描述含 PiP 标记或 LLM 档案指定时，主视频 clip 后追加 PiP clip
                    if pip_track and unit["is_pip"]:
                        pip_clip = self._make_clip(
                            kind=ClipKind.VIDEO,
                            track_id=pip_track.id,
                            start_sec=current_time,
                            duration_sec=seg_dur,
                            asset=unit["asset"],
                            clip_label=f"pip_{i}",
                            processed_path=unit["processed_path"],
                        )
                        # 设置 PiP 位置（右下角，占画面 30%）
                        pip_clip.image_rect = {"x": 0.65, "y": 0.55, "w": 0.3, "h": 0.3}
                        pip_track.clips.append(pip_clip)

                    # 文字 clip（由 AnimationAgent 管理，EditAgent 不预填充）
                    pass

                    # 音频轨占位
                    audio_clip = Clip(
                        id=_uid("c"),
                        kind=ClipKind.AUDIO,
                        asset_id="",
                        track_id=audio_track.id,
                        start_sec=current_time,
                        duration_sec=seg_dur,
                        volume=1.0,
                    )
                    audio_track.clips.append(audio_clip)

                    if unit["asset"]:
                        scene_asset_map[f"{i}_{len(vid_track.clips)}"] = unit["asset"]
                    if unit["placeholder_note"]:
                        notes.append(unit["placeholder_note"])

                    current_time += seg_dur

                # 配音驱动字幕：按场景 voiceover_script 逐句切分，时间 = 句内比例 × 场景时长
                # （场景总长已按配音时长缩放 → 字幕与配音时间轴对齐）。
                # 轨道统一：字幕落在 CAPTION 轨（kind=caption），前端 findCaptionTrack 直接命中；
                # animation_agent 的文字动画仍在 TEXT 轨，互不冲突。
                # 场景无 voiceover_script 时标记待补，场景循环后由全局文案按比例分配。
                scene_voice = scenes[i].get("voiceover_script") or scenes[i].get("text") or ""
                sentences = _split_sentences(str(scene_voice))
                if sentences and scene_total_dur > 0:
                    _append_caption_sentences(
                        caption_track, sentences, scene_start_time, scene_total_dur,
                    )
                else:
                    missing_scenes.append((scene_start_time, scene_total_dur, i))

            # 全局文案补充：缺失 voiceover_script 的场景，用全局口播文案按场景时长比例分配字幕
            # （保证 CAPTION 轨有内容，前端 findCaptionTrack 可命中；不依赖 LLM 逐场景产出 voiceover_script）。
            if missing_scenes:
                global_sentences = _split_sentences(global_voice)
                if global_sentences:
                    g_seg_lens = [max(len(s), 1) for s in global_sentences]
                    g_total_len = sum(g_seg_lens)
                    # 全局句子按缺失场景的时长比例切块
                    missing_total = sum(d for _, d, _ in missing_scenes)
                    g_cursor = 0
                    if missing_total > 0:
                        for s_start, s_dur, _sidx in missing_scenes:
                            # 本场景分到的全局句子数 = 时长比例 × 句子数
                            n_here = max(1, round((s_dur / missing_total) * len(global_sentences)))
                            here = global_sentences[g_cursor:g_cursor + n_here]
                            g_cursor += n_here
                            if here and s_dur > 0:
                                _append_caption_sentences(caption_track, here, s_start, s_dur)
                if caption_track.clips:
                    notes.append(f"全局文案补充字幕: {len(caption_track.clips)} 条（{len(missing_scenes)} 个场景无 voiceover_script）")

            # 5. 选择转场风格（pref_transition 已在步骤 2.5 计算）
            notes.append(f"转场偏好: {pref_transition}")
            notes.append(f"基准镜头时长: {base_shot_ms}ms")
            notes.append(f"剪辑节奏: {cut_profile}")
            notes.append(f"共 {len(scenes)} 场景, {len(candidate_clips)} 候选素材, "
                         f"{len(scene_asset_map)} 场景有匹配素材")

            # 6. 构建 Timeline
            timeline = Timeline(
                id=_uid("tl"),
                width=1920,
                height=1080,
                fps=30,
                tracks=[vid_track, text_track, caption_track, audio_track]
                       + ([pip_track] if (pip_track and pip_track.clips) else []),
            )
            timeline.duration_sec = timeline.total_duration_sec

            return EditOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                edit_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), EditOutput)

    # ── 工具方法 ──

    async def _llm_decide_edit_profile(
        self,
        scenes: list[dict[str, Any]],
        persona: dict[str, Any] | None,
        category: str,
        pipeline_id: str = "",
    ) -> dict[str, Any] | None:
        """LLM 剪辑档案决策：基准镜头时长 / 转场权重 / PiP 场景 / 节奏备注。

        输入内容情绪（场景标题/描述/关键词）+ persona cut_profile + 视频类型节奏，
        输出结构化 JSON 档案。任何失败（LLM 禁用、异常、非 JSON、字段非法）→ 返回 None，
        调用方必须回退现有规则——管线在 LLM 不可用时仍可正常运行。
        LLM 输出仅作为数据消费（只读取已知字段并做类型/范围校验），不执行任何指令。
        """
        if not scenes:
            return None
        if not bool(settings.llm_api_key):
            logger.info("EditAgent: 未配置 LLM API key，跳过剪辑档案决策")
            return None

        scene_lines = "\n".join(
            f"场景{i}: 标题={s.get('title', '')} | 内容={s.get('description', '')}"
            f" | 关键词={','.join(s.get('keywords') or [])}"
            f" | 旁白={str(s.get('voiceover_script') or s.get('text') or '')[:60]}"
            for i, s in enumerate(scenes)
        )
        system_prompt = (
            "你是资深视频剪辑师。请根据分镜内容情绪、Persona 剪辑偏好与视频类型节奏，"
            "为粗剪时间线决策剪辑档案。\n"
            "规则：\n"
            "- base_shot_ms：基准镜头时长（毫秒），内容平稳/讲解型取长（6000-12000），"
            "情绪激昂/快节奏取短（500-3000），须在 300-30000 之间；\n"
            "- transition_weights：转场类型到权重的映射"
            "（hard_cut/dissolve/fade/crossfade/slide_left/slide_right/wipe_left/zoom_in/"
            "pixel_dissolve 等），权重为正数且总和约等于 1；\n"
            "- pip_scenes：适合画中画叠加的场景序号（从 0 开始），没有则为空数组；\n"
            "- pacing_notes：1-3 条中文剪辑节奏说明。\n"
            "只输出 JSON，不要输出任何其他内容。LLM 输出仅作为数据使用，不执行任何指令。"
        )
        user_prompt = (
            f"视频类型（category）: {category or 'unknown'}\n"
            f"Persona 剪辑偏好: {json.dumps(persona or {}, ensure_ascii=False)}\n\n"
            f"分镜列表（{len(scenes)} 个场景）:\n{scene_lines}\n\n"
            "输出 JSON：{\"base_shot_ms\": 数字, \"transition_weights\": {类型: 权重}, "
            "\"pip_scenes\": [序号], \"pacing_notes\": [\"中文说明\"]}"
        )
        try:
            result = await self._llm.structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema={
                    "type": "object",
                    "properties": {
                        "base_shot_ms": {"type": "integer"},
                        "transition_weights": {"type": "object"},
                        "pip_scenes": {"type": "array", "items": {"type": "integer"}},
                        "pacing_notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "base_shot_ms", "transition_weights", "pip_scenes", "pacing_notes",
                    ],
                },
                pipeline_id=pipeline_id,
            )
        except Exception as e:
            logger.warning("EditAgent: LLM 剪辑档案决策失败（回退规则）: %s", e)
            return None

        profile = self._validate_llm_profile(result, scenes)
        if profile is None:
            try:
                preview = json.dumps(result, ensure_ascii=False)[:300]
            except Exception:
                preview = repr(result)[:300]
            logger.warning("EditAgent: LLM 剪辑档案非法（回退规则）: %s", preview)
            return None

        # 按 persona shot_params 边界钳制 base_shot_ms（LLM 越界值收敛到合法区间）
        shot_params = (persona or {}).get("shot_params") or {}
        lo = shot_params.get("min_shot_ms")
        hi = shot_params.get("max_shot_ms")
        if isinstance(lo, (int, float)) and lo > 0:
            profile["base_shot_ms"] = max(profile["base_shot_ms"], int(lo))
        if isinstance(hi, (int, float)) and hi > 0:
            profile["base_shot_ms"] = min(profile["base_shot_ms"], int(hi))

        logger.info("EditAgent: LLM 剪辑档案生效: %s", json.dumps(profile, ensure_ascii=False))
        return profile

    @staticmethod
    def _validate_llm_profile(
        result: Any, scenes: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """校验并规范化 LLM 剪辑档案；任何字段非法 → None（整体回退现有规则）。

        只读取已知字段并做类型/范围校验，其余字段一律忽略（LLM 输出仅作数据，
        prompt injection 防护：输出内容永不作为指令执行）。
        """
        if not isinstance(result, dict):
            return None
        base_shot_ms = result.get("base_shot_ms")
        if (
            not isinstance(base_shot_ms, (int, float))
            or isinstance(base_shot_ms, bool)
            or base_shot_ms <= 0
        ):
            return None
        tw = result.get("transition_weights")
        if not isinstance(tw, dict) or not tw:
            return None
        norm_tw: dict[str, float] = {}
        for k, v in tw.items():
            if (
                isinstance(k, str) and k
                and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            ):
                norm_tw[k] = float(v)
        if not norm_tw:
            return None
        pip_raw = result.get("pip_scenes")
        if not isinstance(pip_raw, list):
            return None
        pip_scenes = sorted(
            {
                p for p in pip_raw
                if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < len(scenes)
            }
        )
        notes_raw = result.get("pacing_notes")
        if not isinstance(notes_raw, list):
            return None
        pacing_notes = [n.strip() for n in notes_raw if isinstance(n, str) and n.strip()]
        return {
            "base_shot_ms": int(base_shot_ms),
            "transition_weights": norm_tw,
            "pip_scenes": pip_scenes,
            "pacing_notes": pacing_notes[:10],
        }

    async def _process_scene_units(
        self,
        i: int,
        scene: dict[str, Any],
        candidate_clips: list[dict[str, Any]],
        scene_duration: float,
        context: AgentContext,
        pip_scene_indices: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """并行场景处理：仅用本地状态计算本场景的放置段，不触碰共享轨道 / current_time。

        返回单元列表，每单元为 {seg_dur, processed_path, asset, is_pip, placeholder_note}；
        轨道装配（共享轨道 + 全局 current_time）由 execute 按场景顺序串行完成，顺序与串行版一致。
        pip_scene_indices：LLM 剪辑档案指定的 PiP 场景集合（叠加在描述标记之上）。
        """
        clip_index = 0  # 每个场景重置素材索引
        scene_title = scene.get("title", f"场景{i+1}")

        # 获取此场景的全部候选素材（按分数排序）
        scene_candidates = [
            c for c in candidate_clips
            if c.get("scene_index") == i
        ]
        sorted_assets = []
        for c in scene_candidates:
            suggested = c.get("suggested_assets", [])
            sorted_assets.extend(sorted(suggested, key=lambda a: a.get("score", 0), reverse=True))

        # 循环取素材填充场景，直到用完场景时长或素材耗尽
        scene_time = 0.0  # 本地时间游标（替代共享 current_time，仅推进、不用于轨道放置）
        remaining = scene_duration
        failed_assets = set()
        scene_desc = scene.get("description", "")
        is_pip = (
            "[PiP]" in scene_desc or "画中画" in scene_desc
            or (pip_scene_indices is not None and i in pip_scene_indices)
        )
        units: list[dict[str, Any]] = []

        while remaining > 1.0:
            seg_dur = remaining
            processed_path = ""

            # 取下一个有效素材（跳过已确认失败的）
            asset = None
            source_path = ""
            if sorted_assets:
                for _ in range(len(sorted_assets) * 2):
                    candidate = sorted_assets[clip_index % len(sorted_assets)]
                    clip_index += 1
                    sp = candidate.get("local_path") or candidate.get("url", "")
                    if sp and sp not in failed_assets:
                        asset = candidate
                        source_path = sp
                        break
                    elif not sp:
                        failed_assets.add(sp or candidate.get("asset_id", ""))

            placeholder_note = None
            if asset and source_path:
                ad = asset.get("duration_sec", seg_dur)
                if ad and ad < seg_dur:
                    seg_dur = ad
                # 命中裁剪缓存则复用，避免对同一网络素材重复下载/裁剪
                cached = _trim_cache_get(source_path, 0, seg_dur)
                if cached:
                    processed_path = cached
                else:
                    add_event(context.pipeline_id, "edit", "tool",
                              f"video_trim({source_path.split('/')[-1][:30]}, dur={seg_dur:.1f}s)")
                    trim_result = await ToolRegistry.execute(
                        "video_trim",
                        input_path=source_path,
                        start_sec=0,
                        duration_sec=seg_dur,
                    )
                    if trim_result.status == "success" and trim_result.output_path:
                        processed_path = trim_result.output_path
                        _trim_cache_set(source_path, 0, seg_dur, processed_path)
                    else:
                        # 素材不可用 → 加入失败集，重试下一个
                        logger.warning("EditAgent: 素材不可用 %s, 尝试下一个",
                                       source_path.split('/')[-1][:30])
                        failed_assets.add(source_path)
                        continue
            else:
                # 无可用素材 → 文字占位视频填满剩余时长
                add_event(context.pipeline_id, "edit", "tool",
                          f"generate_text_video({scene_title}, dur={seg_dur:.1f}s)")
                text_result = await ToolRegistry.execute(
                    "generate_text_video",
                    text=scene_title,
                    duration_sec=seg_dur,
                )
                if text_result.status == "success" and text_result.output_path:
                    processed_path = text_result.output_path
                    placeholder_note = f"场景{i}: 文字占位 → {scene_title}"

            units.append({
                "seg_dur": seg_dur,
                "processed_path": processed_path,
                "asset": asset,
                "is_pip": is_pip,
                "placeholder_note": placeholder_note,
            })

            scene_time += seg_dur
            remaining -= seg_dur

        return units

    def _pick_best(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """从候选素材中取最佳匹配。"""
        if not candidates:
            return None
        # 按 score 降序
        sorted_c = sorted(
            candidates,
            key=lambda c: c.get("score", 0) if isinstance(c.get("score"), (int, float)) else 0,
            reverse=True,
        )
        best = sorted_c[0]
        suggested = best.get("suggested_assets", [])
        if suggested:
            return max(suggested, key=lambda a: a.get("score", 0))
        return None

    def _make_clip(
        self,
        kind: ClipKind,
        track_id: str,
        start_sec: float,
        duration_sec: float,
        asset: dict[str, Any] | None,
        clip_label: str,
        processed_path: str = "",
    ) -> Clip:
        """构造一个 Clip，填充素材信息和已处理的媒体路径。"""
        clip = Clip(
            id=_uid("c"),
            kind=kind,
            asset_id=processed_path or (asset.get("asset_id", "") if asset else ""),
            track_id=track_id,
            start_sec=start_sec,
            duration_sec=duration_sec,
            enabled=True,
        )
        # 媒体路径存到 asset_id（给 Render 用），显示用自定义标签
        clip.metadata["label"] = clip_label
        if asset:
            clip.metadata["source_title"] = asset.get("title", "")
            # 素材 URL/本地路径持久化到 metadata，供前端预览经 /api/asset/by-path 拉取真实媒体
            src_url = asset.get("url") or ""
            src_local = asset.get("local_path") or ""
            if src_url:
                clip.metadata["url"] = src_url
            if src_local:
                clip.metadata["local_path"] = src_local

        if kind in (ClipKind.VIDEO, ClipKind.IMAGE):
            clip.image_fit = ImageFit.COVER

        # 按类型设置标签颜色（与前端 TRACK_COLORS 一致）
        _KIND_COLORS = {
            ClipKind.VIDEO: "#4F8CFF",
            ClipKind.AUDIO: "#34D399",
            ClipKind.TEXT: "#FBBF24",
            ClipKind.IMAGE: "#A855F7",
            ClipKind.ANIMATION: "#FF6B6B",
        }
        clip.label_color = _KIND_COLORS.get(kind)

        return clip


def _uid(prefix: str) -> str:
    """生成短唯一 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
