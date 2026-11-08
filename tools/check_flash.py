"""闪光安全检测（PRD C9 / R-16，验收 V26–V27）。

规则（与参考样本 §六 一致，也是通行光敏性癫痫建议）：**任意 1 秒内，大面积亮度的
往返变化不超过 3 次**。这里把它落成可算的量：

- ``frame_metrics``：逐帧算平均亮度、亮/暗像素占比；
- ``large_area_flip``：判定这一帧是否构成"大面积亮暗翻转"（面积够大、幅度够大）；
- ``analyze``：滑动 1 秒窗统计翻转次数，超阈值即 violation，并给出秒数。

两条重要纪律：
1. **先用已知答案校准**——故意 4Hz 黑白闪的片必须报违规，否则检测器是聋的；
2. 检测器只是度量，**是否拒绝交付是 D14 的产品决策**（默认拒绝）。

用法::

    python -m tools.check_flash out/poc.mp4 --fps 60
    python -m tools.check_flash out/poc.mp4 --json
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

#: 判定"大面积"的像素占比阈值（亮或暗任一侧）。
LARGE_AREA_RATIO = 0.25
#: 判定"亮暗翻转"的最小幅度（0–1 归一化亮度差）。
MIN_FLIP_AMPLITUDE = 0.10
#: 每 1 秒允许的最大往返次数。
MAX_FLIPS_PER_SEC = 3
#: 亮/暗像素定义（0–255）。
BRIGHT_LEVEL = 204
DARK_LEVEL = 51


@dataclass
class FrameMetric:
    mean_luma: float
    bright_ratio: float
    dark_ratio: float


@dataclass
class FlashReport:
    frames: int
    fps: float
    violations: list[dict] = field(default_factory=list)
    per_second_flips: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        head = "闪光安全通过" if self.ok else f"闪光安全不通过（{len(self.violations)} 秒违规）"
        detail = "；".join(
            f"{v['second']:.0f}s 有 {v['flips']} 次大面积翻转" for v in self.violations[:5]
        )
        return f"{head}：{self.frames} 帧 @ {self.fps:g}fps" + (f"；{detail}" if detail else "")


def frame_metrics(frame: np.ndarray) -> FrameMetric:
    """单帧指标。``frame`` 为 HxW 或 HxWx3，取值 0–255。"""
    arr = np.asarray(frame)
    if arr.ndim == 3:
        # 亮度近似（ITU-R BT.601），避免引入色彩管理依赖
        grey = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    else:
        grey = arr.astype(np.float64)
    total = float(grey.size) or 1.0
    return FrameMetric(
        mean_luma=float(grey.mean() / 255.0),
        bright_ratio=float((grey >= BRIGHT_LEVEL).sum() / total),
        dark_ratio=float((grey <= DARK_LEVEL).sum() / total),
    )


def large_area_flip(prev: FrameMetric, cur: FrameMetric) -> bool:
    """两帧之间是否构成"大面积亮暗翻转"。"""
    amplitude = abs(cur.mean_luma - prev.mean_luma)
    if amplitude < MIN_FLIP_AMPLITUDE:
        return False
    area = max(cur.bright_ratio, cur.dark_ratio, prev.bright_ratio, prev.dark_ratio)
    return area >= LARGE_AREA_RATIO


def analyze(
    metrics: Sequence[FrameMetric],
    fps: float,
    *,
    max_flips_per_sec: int = MAX_FLIPS_PER_SEC,
) -> FlashReport:
    """按 1 秒滑动窗统计翻转次数并给出违规秒。"""
    if fps <= 0:
        raise ValueError("fps 必须为正")
    flips: list[tuple[float, float]] = []  # (时间秒, 幅度)
    for index in range(1, len(metrics)):
        prev, cur = metrics[index - 1], metrics[index]
        if large_area_flip(prev, cur):
            flips.append((index / fps, abs(cur.mean_luma - prev.mean_luma)))

    report = FlashReport(frames=len(metrics), fps=fps)
    if not flips:
        return report

    # 以每个翻转时刻为窗口起点，检查 [t, t+1) 内的往返次数
    for second in range(0, max(1, math.ceil(len(metrics) / fps))):
        window_start, window_end = float(second), float(second + 1)
        in_window = [f for f in flips if window_start <= f[0] < window_end]
        report.per_second_flips.append({"second": second, "flips": len(in_window)})
        if len(in_window) > max_flips_per_sec:
            report.violations.append(
                {
                    "second": window_start,
                    "flips": len(in_window),
                    "limit": max_flips_per_sec,
                }
            )
    return report


def analyze_video(
    path: str | Path,
    *,
    fps: float = 60.0,
    width: int = 160,
    height: int = 90,
) -> FlashReport:
    """解码视频为缩略帧后分析（缩略是为了速度，亮度统计不受影响）。"""
    video = Path(path)
    if not video.exists():
        raise FileNotFoundError(video)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("未找到 ffmpeg")
    proc = subprocess.run(
        [
            ffmpeg, "-v", "error", "-i", str(video),
            "-vf", f"scale={width}:{height}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 解码失败：{proc.stderr.decode('utf-8', 'replace')[:400]}"
        )
    frame_size = width * height * 3
    buffer = np.frombuffer(proc.stdout, dtype=np.uint8)
    count = buffer.size // frame_size
    metrics = [
        frame_metrics(buffer[i * frame_size : (i + 1) * frame_size].reshape(height, width, 3))
        for i in range(count)
    ]
    return analyze(metrics, fps)


def iter_metrics_from_raw(data: bytes, frame_size: int) -> Iterable[FrameMetric]:
    """供单测复用：从 rgb24 原始字节流逐帧产出指标。"""
    for offset in range(0, len(data) - frame_size + 1, frame_size):
        chunk = np.frombuffer(data[offset : offset + frame_size], dtype=np.uint8)
        yield frame_metrics(chunk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="闪光安全检测（每秒 ≤3 次大面积往返）")
    parser.add_argument("video", help="待检测视频")
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = analyze_video(args.video, fps=args.fps, width=args.width, height=args.height)
    except Exception as exc:  # noqa: BLE001 - CLI 边界
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "frames": report.frames,
                    "fps": report.fps,
                    "violations": report.violations,
                    "per_second_flips": report.per_second_flips,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(report.summary())
        for violation in report.violations:
            print(
                f"  - {violation['second']:.0f}s：{violation['flips']} 次翻转"
                f"（上限 {violation['limit']}）"
            )
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
