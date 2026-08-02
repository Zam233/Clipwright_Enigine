"""帧验证工具 — 用 FFmpeg signalstats/blackdetect 检测黑帧/白帧/过曝帧。

真实实现（替代 tool/stubs.py 中的占位 FrameValidatorTool）：
- signalstats：逐帧输出 YAVG/YMIN/YMAX 亮度统计（metadata=print 到 stdout）。
- blackdetect：检测连续黑帧片段（日志输出到 stderr）。
- 采样：对片段均匀采样 ≤1s 的样本段，逐段分类后聚合为整片结论。

输出契约（消费方：MaterialAgent._validate_video_frame / QualityAgent）：
    {valid, is_blank, is_white, is_overexposed, sample_count, match_score}
- match_score = 无任何问题的样本段占比。
- 路径缺失 / ffmpeg 失败 / 超过 30s 超时 → valid=False + error，绝不抛异常。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import resolve_ffmpeg, resolve_ffprobe

# ── 亮度阈值（视频范围 YUV：黑=16、白=235，全范围时黑=0、白=255）──
_BLANK_YAVG_MAX = 8.0  # 规格：样本段 YAVG < 8 → 黑帧
_BLACK_YMAX_MAX = 24.0  # 全帧 YMAX ≤ 24 → 该帧视为"黑"，用于视频范围黑=16
_WHITE_YAVG_MAX = 250.0  # 规格：YAVG > 250 → 白帧（全范围白）
_WHITE_YMIN_MIN = 230.0  # 全帧 YMIN ≥ 230 → 该帧视为"全白"，用于视频范围白=235
_OVEREXPOSED_YAVG_MIN = 235.0  # 规格：YAVG > 235 且持续 → 过曝
_OVEREXPOSED_FRAME_RATIO = 0.5  # "持续"：超过半数帧 YAVG > 235
_RATIO_THRESHOLD = 0.9  # 全黑/全白帧占比 ≥ 90% → 判为该类

_MAX_SAMPLES = 5
_SEGMENT_DURATION_MAX = 1.0
_SEGMENT_DURATION_MIN = 0.3
_FFMPEG_TIMEOUT_SEC = 30.0
_PROBE_TIMEOUT_SEC = 30.0

_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_YMIN_RE = re.compile(r"lavfi\.signalstats\.YMIN=(\d+)")
_YMAX_RE = re.compile(r"lavfi\.signalstats\.YMAX=(\d+)")


def _frame_stats(
    yavg_list: list[float], ymin_list: list[int], ymax_list: list[int]
) -> list[tuple[float, int, int]]:
    """把逐帧的 YAVG/YMIN/YMAX 列表对齐为 (yavg, ymin, ymax) 帧元组。"""
    return list(zip(yavg_list, ymin_list, ymax_list))


def _classify_segment(
    frames: list[tuple[float, int, int]], blackdetect_black: bool
) -> tuple[bool, bool, bool]:
    """对单个采样段分类，返回 (is_blank, is_white, is_overexposed)。"""
    if not frames:
        return False, False, False
    n = len(frames)
    mean_yavg = sum(f[0] for f in frames) / n
    all_black_ratio = sum(1 for f in frames if f[2] <= _BLACK_YMAX_MAX) / n
    all_white_ratio = sum(1 for f in frames if f[1] >= _WHITE_YMIN_MIN) / n
    over_ratio = sum(1 for f in frames if f[0] > _OVEREXPOSED_YAVG_MIN) / n

    is_blank = (
        mean_yavg < _BLANK_YAVG_MAX
        or all_black_ratio >= _RATIO_THRESHOLD
        or blackdetect_black
    )
    is_white = (
        mean_yavg > _WHITE_YAVG_MAX
        or all_white_ratio >= _RATIO_THRESHOLD
    )
    is_overexposed = (
        mean_yavg > _OVEREXPOSED_YAVG_MIN
        and over_ratio >= _OVEREXPOSED_FRAME_RATIO
    )
    return is_blank, is_white, is_overexposed


def _sample_plan(duration: float) -> list[tuple[float, float]]:
    """生成均匀采样计划，返回 [(start_sec, seg_dur_sec), ...]。"""
    sample_count = max(1, min(_MAX_SAMPLES, math.ceil(duration / 8.0)))
    seg_dur = min(_SEGMENT_DURATION_MAX, max(_SEGMENT_DURATION_MIN, duration / sample_count))
    plan: list[tuple[float, float]] = []
    for i in range(sample_count):
        center = (i + 0.5) * duration / sample_count
        start = max(0.0, center - seg_dur / 2.0)
        plan.append((start, seg_dur))
    return plan


async def _probe_duration(path: str) -> float | None:
    """用 ffprobe 获取时长（秒），失败返回 None。"""
    cmd = [
        resolve_ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=duration",
        "-of",
        "json",
        path,
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SEC
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.debug("FrameValidator: ffprobe 调用失败: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("FrameValidator: ffprobe 失败: %s", result.stderr.strip()[:300])
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    format_duration = data.get("format", {}).get("duration")
    if format_duration not in (None, "N/A"):
        try:
            value = float(format_duration)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    for stream in data.get("streams", []):
        candidate = stream.get("duration")
        if candidate not in (None, "N/A"):
            try:
                value = float(candidate)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return None


async def _analyze_segment(
    path: str, start: float, seg_dur: float
) -> tuple[list[float], list[int], list[int], bool]:
    """对单个采样段运行 ffmpeg，返回 (yavg_list, ymin_list, ymax_list, blackdetect_black)。"""
    cmd = [
        resolve_ffmpeg(),
        "-v",
        "info",
        "-ss",
        f"{start:.3f}",
        "-i",
        path,
        "-t",
        f"{seg_dur:.3f}",
        "-vf",
        "blackdetect=d=0.1:pix_th=0.10,signalstats,metadata=print:file=-",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SEC
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:500] or f"ffmpeg exit code {result.returncode}")
    yavg_list = [float(m) for m in _YAVG_RE.findall(result.stdout or "")]
    ymin_list = [int(m) for m in _YMIN_RE.findall(result.stdout or "")]
    ymax_list = [int(m) for m in _YMAX_RE.findall(result.stdout or "")]
    blackdetect_black = "black_start:" in (result.stderr or "")
    return yavg_list, ymin_list, ymax_list, blackdetect_black


def _failure_output(error: str) -> dict[str, Any]:
    """构造失败输出：保持契约字段完整 + error。"""
    return {
        "valid": False,
        "is_blank": False,
        "is_white": False,
        "is_overexposed": False,
        "sample_count": 0,
        "match_score": 0.0,
        "error": error,
    }


class FrameValidatorTool(BaseTool):
    """帧验证工具 — 检测黑帧/过曝/全白帧，过滤不合格素材。"""
    name = "frame_validator"
    description = "帧验证：检测黑帧/过曝/全白帧，过滤不合格素材（FFmpeg signalstats + blackdetect）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        video_url: str = "",
        expected_text: str = "",
        local_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        """验证视频帧的亮度健康度。

        Args:
            video_url: 视频路径或 URL（本地文件路径优先走 local_path）。
            expected_text: 兼容保留参数（未使用）。
            local_path: 本地视频路径（优先于 video_url）。
        """
        # expected_text 为兼容保留参数；本工具只做亮度健康度检测，不校验文字内容。
        source = str(local_path or video_url or kwargs.get("path") or kwargs.get("video_path") or "").strip()
        if not source:
            error = "missing video path"
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )
        if not Path(source).is_file():
            error = f"file not found: {source[:120]}"
            logger.debug("FrameValidator: %s", error)
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )

        duration = await _probe_duration(source)
        if not duration:
            error = "failed to probe video duration"
            logger.debug("FrameValidator: %s (%s)", error, source[:120])
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )

        plan = _sample_plan(duration)
        issues: list[bool] = []
        total_frames = 0
        clip_flags = {"is_blank": False, "is_white": False, "is_overexposed": False}
        try:
            for start, seg_dur in plan:
                try:
                    yavg_list, ymin_list, ymax_list, blackdetect_black = await _analyze_segment(
                        source, start, seg_dur
                    )
                except subprocess.TimeoutExpired:
                    error = f"ffmpeg timed out after {_FFMPEG_TIMEOUT_SEC:.0f}s"
                    logger.debug("FrameValidator: %s (%s)", error, source[:120])
                    return ToolExecResult(
                        status=ToolStatus.SUCCESS, tool_name=self.name,
                        output=_failure_output(error), error=error,
                    )
                frames = _frame_stats(yavg_list, ymin_list, ymax_list)
                total_frames += len(frames)
                is_blank, is_white, is_overexposed = _classify_segment(frames, blackdetect_black)
                issues.append(is_blank or is_white or is_overexposed)
                clip_flags["is_blank"] = clip_flags["is_blank"] or is_blank
                clip_flags["is_white"] = clip_flags["is_white"] or is_white
                clip_flags["is_overexposed"] = clip_flags["is_overexposed"] or is_overexposed
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            error = f"ffmpeg error: {exc}"
            logger.debug("FrameValidator: %s (%s)", error, source[:120])
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )
        except Exception as exc:  # noqa: BLE001 — 契约要求绝不抛异常
            error = f"frame validation error: {exc}"
            logger.debug("FrameValidator: %s (%s)", error, source[:120])
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )

        if total_frames == 0:
            error = "no frames analyzed by ffmpeg"
            logger.debug("FrameValidator: %s (%s)", error, source[:120])
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output=_failure_output(error), error=error,
            )

        sample_count = len(plan)
        match_score = round((sample_count - sum(issues)) / sample_count, 4)
        output: dict[str, Any] = {
            "valid": True,
            "is_blank": clip_flags["is_blank"],
            "is_white": clip_flags["is_white"],
            "is_overexposed": clip_flags["is_overexposed"],
            "sample_count": sample_count,
            "match_score": match_score,
        }
        logger.debug(
            "FrameValidator: %s valid=%s blank=%s white=%s over=%s match=%s",
            source[:60], output["valid"], output["is_blank"],
            output["is_white"], output["is_overexposed"], match_score,
        )
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output=output)
