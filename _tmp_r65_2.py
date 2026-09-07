"""轮65 补丁2：target_loudness 接线（Timeline 元数据 → 渲染 loudnorm）。执行后删除。"""
from pathlib import Path

# ── Timeline 增加 metadata 字段 ──
p = Path("clipwright/schema/timeline.py")
src = p.read_text(encoding="utf-8")
old = '''    tracks: list[Track] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list, description="时间轴标记列表（M8）")'''
new = '''    tracks: list[Track] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list, description="时间轴标记列表（M8）")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="时间线扩展元数据（如 target_loudness_lufs → 渲染响度归一目标）")'''
assert src.count(old) == 1, f"timeline meta={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("timeline metadata field added")

# ── PIPE：audio_config 透传 persona 目标响度 ──
p = Path("clipwright/services/pipeline_v2.py")
src = p.read_text(encoding="utf-8")
old = '''                    "voice_id": extra_params.get("voice_id")
                    or persona_config.get("audio", {}).get("voice_clone_model_id")
                    or persona_config.get("audio", {}).get("voice")
                    or "",'''
new = '''                    "voice_id": extra_params.get("voice_id")
                    or persona_config.get("audio", {}).get("voice_clone_model_id")
                    or persona_config.get("audio", {}).get("voice")
                    or "",
                    # 批8：Persona 目标响度透传（渲染 loudnorm 消费；缺省 -16）
                    "target_loudness_lufs": persona_config.get("audio", {}).get(
                        "target_loudness_lufs"),'''
assert src.count(old) == 1, f"pipe loud anchor={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("pipe loudness wired")

# ── audio_agent：写入 timeline.metadata ──
p = Path("clipwright/agents/audio_agent.py")
src = p.read_text(encoding="utf-8")
old = '''            audio_config = input_data.audio_config or {}'''
new = '''            audio_config = input_data.audio_config or {}
            # 批8：Persona 目标响度 → 时间线元数据（渲染端 loudnorm 消费）
            _loud = audio_config.get("target_loudness_lufs")
            if _loud is not None:
                try:
                    timeline.metadata["target_loudness_lufs"] = float(_loud)
                except (TypeError, ValueError):
                    pass'''
assert src.count(old) == 1, f"audio meta anchor={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("audio_agent loudness done")

# ── render：loudnorm I 值从时间线元数据读取 ──
p = Path("clipwright/services/render.py")
src = p.read_text(encoding="utf-8")

# render() 主函数中提取（timeline 可用处）：挂在 _mix_audio_safe 调用前
old = '''        # 音频（C12：混合失败必须标记到结果，而非静默静音成片）
        audio_warnings: list[str] = []
        if final_video:
            if progress_callback:
                await progress_callback("audio", 98, "混流音频")
            final_video, mix_marker = await self._mix_audio_safe(
                final_video, audio_segments, audio_file_path, bitrate, audio_bitrate, bgm_file_path,
                video_cached=video_from_cache)'''
new = '''        # 音频（C12：混合失败必须标记到结果，而非静默静音成片）
        audio_warnings: list[str] = []
        # 批8：Persona 目标响度（时间线元数据）→ loudnorm I 值
        loudness_target = None
        if timeline_obj is not None:
            try:
                loudness_target = float(
                    (getattr(timeline_obj, "metadata", {}) or {}).get("target_loudness_lufs"))
            except (TypeError, ValueError):
                loudness_target = None
        if final_video:
            if progress_callback:
                await progress_callback("audio", 98, "混流音频")
            final_video, mix_marker = await self._mix_audio_safe(
                final_video, audio_segments, audio_file_path, bitrate, audio_bitrate, bgm_file_path,
                video_cached=video_from_cache, loudness_target=loudness_target)'''
assert src.count(old) == 1, f"render call anchor={src.count(old)}"
src = src.replace(old, new)

# _mix_audio_safe/_mix_audio 签名与透传
old = '''    async def _mix_audio_safe(self, video, segments, out, afp="", ab="192k", bfp="", bitrate="5M",
                              video_cached: bool = False):'''
new = '''    async def _mix_audio_safe(self, video, segments, out, afp="", ab="192k", bfp="", bitrate="5M",
                              video_cached: bool = False, loudness_target: float | None = None):'''
assert src.count(old) == 1, f"safe sig={src.count(old)}"
src = src.replace(old, new)
old = '''        try:
            await self._mix_audio(video, segments, out, audio_path, ab, bgm_path, bitrate,
                                  video_cached=video_cached)'''
new = '''        try:
            await self._mix_audio(video, segments, out, audio_path, ab, bgm_path, bitrate,
                                  video_cached=video_cached, loudness_target=loudness_target)'''
assert src.count(old) == 1, f"safe call={src.count(old)}"
src = src.replace(old, new)
old = '''    async def _mix_audio(self, input_video, segments, output_path, afp="", ab="192k", bfp="", bitrate="5M",
                         video_cached: bool = False):'''
new = '''    async def _mix_audio(self, input_video, segments, output_path, afp="", ab="192k", bfp="", bitrate="5M",
                         video_cached: bool = False, loudness_target: float | None = None):'''
assert src.count(old) == 1, f"mix sig={src.count(old)}"
src = src.replace(old, new)

old = '''                chains.append(
                    f"{chains_mix_in}amix=inputs={len(mix_inputs)}:duration=longest:normalize=0,"
                    f"loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
                )'''
new = '''                # 批8：响度目标可由 Persona audio.target_loudness_lufs 指定（缺省 -16）
                _lufs = f"{loudness_target:g}" if loudness_target is not None else "-16"
                chains.append(
                    f"{chains_mix_in}amix=inputs={len(mix_inputs)}:duration=longest:normalize=0,"
                    f"loudnorm=I={_lufs}:LRA=11:TP=-1.5[aout]"
                )'''
assert src.count(old) == 1, f"loudnorm anchor={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("render loudnorm wired")
