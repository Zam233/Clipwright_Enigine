"""成片质量核对：文字动画是否**真的**画在画面中央、并且**真的**在动（PRD text-animation-llm M3）。

为什么要这个模块：M1/M2 只能证明"关键帧写对了"，证明不了"用户看到的是动的文字、而且没
压到字幕带"。这里直接对着**已渲染的 mp4** 取帧做像素统计，两条可量化事实：

1. `band_ink`：按横带算"墨迹率"（偏离背景中位数的像素占比）。中心带墨迹率明显高于控制窗口
   = 文字确实落在画面中央（不是被推到字幕区、也不是根本没渲染出来）；
2. `motion`：窗口内相邻帧的平均绝对差。文字静止时几乎为 0，有入场/位移/缩放时会显著抬升
   = 动画没被冻在最后一帧（旧 `_build_mg_chained_cmd` 少了 `setpts` 就是这个症状）。

只用 ffmpeg + 标准库（不引 numpy），方便在 CI 与现场随手跑：
`python -m clipwright.services.render_qa <video> --window 12,3`
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Sequence

#: 取帧宽度（越小越快；文字检测够用）
ANALYSIS_WIDTH = 160
#: 采样帧率
ANALYSIS_FPS = 8
#: 与中位数的差异超过这个灰度值算"墨迹"（文字/图形边缘）
INK_THRESHOLD = 24
#: 横带划分（按帧高比例）：上/中/字幕带
BANDS: dict[str, tuple[float, float]] = {
    "top": (0.0, 0.30),
    "center": (0.30, 0.70),
    "caption": (0.84, 1.0),
}


@dataclass
class WindowStats:
    start_sec: float
    duration_sec: float
    frames: int = 0
    ink: dict[str, float] = field(default_factory=dict)
    motion: float = 0.0

    def to_public(self) -> dict:
        return {
            "start_sec": round(self.start_sec, 3),
            "duration_sec": round(self.duration_sec, 3),
            "frames": self.frames,
            "ink": {k: round(v, 4) for k, v in self.ink.items()},
            "motion": round(self.motion, 3),
        }


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def _gray_frames(
    video: str,
    start_sec: float,
    duration_sec: float,
    *,
    width: int = ANALYSIS_WIDTH,
    fps: int = ANALYSIS_FPS,
) -> tuple[bytes, int, int]:
    """取一段窗口的灰度帧（`fps=8, scale=160:-2, gray`）→ (raw bytes, 宽, 高, 帧数)。"""
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin",
        "-ss", f"{max(0.0, start_sec):.3f}", "-t", f"{max(0.05, duration_sec):.3f}",
        "-i", video,
        "-vf", f"fps={fps},scale={width}:-2,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=False)
    if raw.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 取帧失败：{raw.stderr.decode('utf-8', 'ignore')[:200]}")
    # 高 = 总字节 / 宽 / 帧数；帧数未知，用探测得到的尺寸反推
    height = _probe_height(video, width)
    frame_bytes = width * height
    frames = len(raw.stdout) // frame_bytes if frame_bytes else 0
    return raw.stdout[: frames * frame_bytes], width, height


_PROBE_CACHE: dict[tuple[str, int], int] = {}


def _probe_height(video: str, width: int) -> int:
    key = (video, width)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video],
        capture_output=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError("ffprobe 读取分辨率失败")
    stream = json.loads(out.stdout.decode("utf-8", "ignore"))["streams"][0]
    src_w, src_h = int(stream["width"]), int(stream["height"])
    height = max(2, round(src_h * width / src_w / 2) * 2)
    _PROBE_CACHE[key] = height
    return height


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def analyze_window(
    video: str,
    start_sec: float,
    duration_sec: float,
    *,
    bands: dict[str, tuple[float, float]] | None = None,
) -> WindowStats:
    """统计一个时间窗口的横带墨迹率与帧间运动量。"""
    bands = bands or BANDS
    raw, width, height = _gray_frames(video, start_sec, duration_sec)
    frame_bytes = width * height
    stats = WindowStats(start_sec=start_sec, duration_sec=duration_sec)

    frames: list[bytes] = [
        raw[i * frame_bytes: (i + 1) * frame_bytes]
        for i in range(len(raw) // frame_bytes)
    ]
    stats.frames = len(frames)
    if not frames:
        stats.ink = {name: 0.0 for name in bands}
        return stats

    # 用第一帧的中位数当背景基准（纯色/渐变背景也适用）
    base = _median(list(frames[0]))
    for name, (a, b) in bands.items():
        row_from = max(0, int(a * height))
        row_to = max(row_from + 1, int(b * height))
        inked = 0
        total = 0
        for frame in frames:
            for row in range(row_from, row_to):
                chunk = frame[row * width:(row + 1) * width]
                inked += sum(1 for px in chunk if abs(px - base) > INK_THRESHOLD)
                total += len(chunk)
        stats.ink[name] = (inked / total) if total else 0.0

    if len(frames) > 1:
        diffs = 0
        for prev, cur in zip(frames, frames[1:]):
            diffs += sum(abs(x - y) for x, y in zip(prev, cur))
        stats.motion = diffs / ((len(frames) - 1) * frame_bytes)
    return stats


def analyze_windows(
    video: str,
    windows: Iterable[Sequence[float]],
) -> list[WindowStats]:
    return [analyze_window(video, float(w[0]), float(w[1])) for w in windows]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="成片文字动画核对（墨迹带 + 运动量）")
    parser.add_argument("video")
    parser.add_argument("--window", action="append", default=[],
                        help="起始秒,时长秒（可多次）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not ffmpeg_available():
        print("ffmpeg 不可用")
        return 2
    windows = [tuple(float(x) for x in w.split(",")) for w in args.window] or [(0.0, 1.0)]
    out = [s.to_public() for s in analyze_windows(args.video, windows)]
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ANALYSIS_FPS", "ANALYSIS_WIDTH", "BANDS", "INK_THRESHOLD", "WindowStats",
    "analyze_window", "analyze_windows", "ffmpeg_available",
]
