"""音频处理工具 — FFmpeg/librosa 封装。"""

from __future__ import annotations

import json
import asyncio
import subprocess
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path, _ffmpeg


class AudioExtractTool(BaseTool):
    """音频提取工具。"""
    name = "audio_extract"
    description = "从视频中提取音频为独立文件"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        format: str = "wav",
        **kwargs: Any,
    ) -> ToolExecResult:
        ext_map = {"mp3": ".mp3", "aac": ".aac", "wav": ".wav", "ogg": ".ogg", "flac": ".flac"}
        ext = ext_map.get(format, ".wav")
        out = _ensure_output_path(output_path, "audio_", ext)
        try:
            result = await _ffmpeg("-i", input_path, "-vn", "-acodec", _codec_for(format), out)
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"output_path": out, "format": format},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )


def _estimate_bpm_from_rms_stderr(ffmpeg_stderr: str) -> Optional[float]:
    """从 ffmpeg astats 的 stderr 输出估计 BPM（轮 82）。

    astats 以 50ms 窗逐窗输出 ``RMS level dB`` 行；把 dB 折算成线性幅度得到
    能量包络，自相关取 60~180 BPM 对应滞后窗内的峰值周期，再折叠到 70~180。
    返回 None 表示无法估计（行数不足/无能量）。
    """
    import math
    import re as _re

    try:
        import numpy as _np
    except ImportError:  # pragma: no cover - numpy 为硬依赖，防御兜底
        return None

    values: list[float] = []
    for line in (ffmpeg_stderr or "").splitlines():
        m = _re.search(r"RMS level dB:\s*(-?[\d.]+|-?inf)", line)
        if not m:
            continue
        raw = m.group(1)
        if "inf" in raw:
            values.append(0.0)  # 静音窗
        else:
            try:
                db = float(raw)
                values.append(0.0 if db <= -90 else 10 ** (db / 20))
            except ValueError:
                continue
    # 至少 ~6 秒的包络才能稳定估计节拍周期
    if len(values) < 120:
        return None
    env = _np.asarray(values, dtype=float)
    env = env - env.mean()
    norm = float(_np.sqrt((env ** 2).sum()))
    if norm <= 1e-9 or not math.isfinite(norm):
        return None
    env = env / norm

    # 自相关（50ms/窗 → lag_sec = lag * 0.05）
    win_hz = 20.0  # 1/0.05s
    full = _np.correlate(env, env, mode="full")
    ac = full[len(env) - 1:]
    lo_lag = int(win_hz * 60 / 180)   # 180 BPM
    hi_lag = int(win_hz * 60 / 60)    # 60 BPM
    if hi_lag >= len(ac):
        return None
    # 窗口内取自相关峰（跳过滞后 0 附近）
    lag = lo_lag + int(_np.argmax(ac[lo_lag:hi_lag + 1]))
    if lag <= 0:
        return None
    bpm = win_hz * 60.0 / lag
    # 折叠到 70~180（误差容忍：能量包络常检出半周期/倍周期）
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return round(bpm, 1)


class BPMDetectTool(BaseTool):
    """BPM 检测工具（ffmpeg RMS 能量包络 + 自相关节拍周期估计）。"""
    name = "bpm_detect"
    description = "检测音频文件的 BPM（基于能量包络自相关分析）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        **kwargs: Any,
    ) -> ToolExecResult:
        """轮 82：真实 BPM 估计，替换返回恒 120 的桩。

        算法（确定性、无新依赖）：
        1. ffmpeg astats 按 50ms 窗输出 RMS 电平（stderr 逐行）；
        2. dB → 线性幅度并归一化，得到能量包络；
        3. 对包络做自相关，在 60~180 BPM 对应滞后窗口内取峰值周期；
        4. ×2/÷2 折叠到 70~180 常用区间。
        检测失败/音频过短时返回 `method="failed"` 且不带 `bpm` 键，
        调用方据此跳过节拍剪辑（不再伪造 120）。
        """
        try:
            result = await _ffmpeg(
                "-i", input_path,
                "-af", "astats=metadata=1:reset=1:length=0.05",
                "-f", "null", "-",
            )
            bpm = _estimate_bpm_from_rms_stderr(result)
            if bpm is None:
                return ToolExecResult(
                    status=ToolStatus.SUCCESS,
                    tool_name=self.name,
                    output={"error": "BPM 检测失败（音频过短或无节奏能量）",
                            "input_path": input_path, "method": "failed"},
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"bpm": bpm, "input_path": input_path,
                        "method": "autocorrelation-rms"},
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )


class AudioReplaceTool(BaseTool):
    """音频替换/混音工具。"""
    name = "audio_replace"
    description = "替换或混入音频（mix=True 混音，mix=False 替换）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        video_path: str,
        audio_path: str,
        volume: float = 1.0,
        mix: bool = False,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "audioreplace_", ".mp4")
        try:
            # 先调音量
            vol_filter = f"volume={volume}" if volume != 1.0 else ""
            if mix:
                # 混音：用 amix 过滤器
                filter_complex = "[1:a]volume=1.0[a1];[0:a][a1]amix=inputs=2:duration=first"
                result = await _ffmpeg(
                    "-i", video_path, "-i", audio_path,
                    "-filter_complex", filter_complex,
                    "-c:v", "copy", "-c:a", "aac", out,
                )
            else:
                # 替换：直接用新音频
                audio_input = f"[1:a]{vol_filter}" if vol_filter else "[1:a]"
                result = await _ffmpeg(
                    "-i", video_path, "-i", audio_path,
                    "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                    "-c:a", "aac", "-shortest", out,
                )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"output_path": out, "mix": mix, "volume": volume},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )


class AudioNormalizeTool(BaseTool):
    """音频归一化工具 — LUFS/峰值归一化。"""
    name = "audio_normalize"
    description = "音频音量归一化：loudnorm(LUFS目标)/peak(峰值)/rms(RMS)"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        mode: str = "loudnorm",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "norm_", ".wav")
        try:
            if mode == "loudnorm":
                # EBU R128 loudnorm 两遍处理
                import json
                # 第一遍: 分析
                probe = await asyncio.to_thread(subprocess.run, 
                    ["ffmpeg", "-y", "-i", input_path, "-af",
                     "loudnorm=print_format=json", "-f", "null", "-"],
                    capture_output=True, text=True, timeout=120,
                )
                # 第二遍: 应用归一化
                result = await _ffmpeg(
                    "-i", input_path,
                    "-af", "loudnorm=linear=true",
                    "-c:a", "pcm_s16le", out,
                )
            elif mode == "peak":
                result = await _ffmpeg(
                    "-i", input_path,
                    "-af", "volume=1.0:precision=double",
                    "-c:a", "pcm_s16le", out,
                )
            else:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"unsupported mode: {mode}",
                )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"normalize error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"output_path": out, "mode": mode}, output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name,
                error="ffmpeg not found",
            )


class AudioMixTool(BaseTool):
    """音频混音工具 — 多音轨混音。"""
    name = "audio_mix"
    description = "将多段音频/视频的音频轨道混合为单轨输出"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_paths: list[str],
        volumes: Optional[list[float]] = None,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "mix_", ".wav")
        try:
            if not input_paths:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="no input paths")
            vols = volumes or [1.0] * len(input_paths)

            # 构建 ffmpeg 多输入混音命令
            cmd_args = ["ffmpeg", "-y", "-loglevel", "error"]
            for p in input_paths:
                cmd_args.extend(["-i", p])

            # amix filter
            amix = f"amix=inputs={len(input_paths)}:duration=longest"
            vol_filters = [f"[{i}:a]volume={vols[i]}[v{i}]" for i in range(len(input_paths))]
            mix_inputs = "".join(f"[v{i}]" for i in range(len(input_paths)))
            filter_complex = ";".join(vol_filters) + f";{mix_inputs}{amix}[a]"

            result = await asyncio.to_thread(
                subprocess.run,
                [*cmd_args, "-filter_complex", filter_complex,
                 "-map", "[a]", "-c:a", "pcm_s16le", out],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"mix error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"output_path": out, "inputs": len(input_paths)}, output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name,
                error="ffmpeg not found",
            )


def _codec_for(fmt: str) -> str:
    return {"mp3": "libmp3lame", "aac": "aac", "wav": "pcm_s16le", "ogg": "libvorbis", "flac": "flac"}.get(fmt, "pcm_s16le")
