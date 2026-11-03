"""音画同步检查（PRD C7 / R-19，验收 V32）。

要检查的两件事：
1. **时长对齐**：视频时长 ≈ 音频时长 + 片头（``preroll_sec``）；
2. **片头偏移**：负时间片头（参考样本的闪光警告页）必须把音频延后同样时长混入，
   否则整片音画会漂移 ``preroll`` 秒。

本模块只做度量与判定，不修改媒体；CLI 用 ffprobe 取真实时长。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: 允许的同步误差（秒）——1 帧 @ 60fps ≈ 0.017s，这里给到 0.05s。
MAX_OFFSET_SEC = 0.05


@dataclass
class SyncReport:
    video_sec: float
    audio_sec: float
    preroll_sec: float = 0.0
    tolerance_sec: float = MAX_OFFSET_SEC

    @property
    def expected_video_sec(self) -> float:
        return self.audio_sec + self.preroll_sec

    @property
    def delta_sec(self) -> float:
        return self.video_sec - self.expected_video_sec

    @property
    def ok(self) -> bool:
        return abs(self.delta_sec) <= self.tolerance_sec

    def summary(self) -> str:
        head = "音画同步通过" if self.ok else "音画不同步"
        return (
            f"{head}：视频 {self.video_sec:.3f}s / 音频 {self.audio_sec:.3f}s"
            f"（片头 {self.preroll_sec:.3f}s，期望 {self.expected_video_sec:.3f}s，"
            f"偏差 {self.delta_sec:+.3f}s，容差 ±{self.tolerance_sec:.3f}s）"
        )

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "video_sec": round(self.video_sec, 3),
            "audio_sec": round(self.audio_sec, 3),
            "preroll_sec": round(self.preroll_sec, 3),
            "expected_video_sec": round(self.expected_video_sec, 3),
            "delta_sec": round(self.delta_sec, 3),
            "tolerance_sec": self.tolerance_sec,
        }


def check_sync(
    video_sec: float,
    audio_sec: float,
    *,
    preroll_sec: float = 0.0,
    tolerance_sec: float = MAX_OFFSET_SEC,
) -> SyncReport:
    """纯逻辑检查（便于单测）。"""
    if video_sec < 0 or audio_sec < 0:
        raise ValueError("时长不能为负")
    if preroll_sec < 0:
        raise ValueError("片头时长不能为负")
    return SyncReport(
        video_sec=float(video_sec),
        audio_sec=float(audio_sec),
        preroll_sec=float(preroll_sec),
        tolerance_sec=float(tolerance_sec),
    )


def probe_duration(path: str | Path) -> float:
    """用 ffprobe 取媒体时长（秒）。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("未找到 ffprobe")
    proc = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(file_path),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{proc.stderr[:300]}")
    payload = json.loads(proc.stdout or "{}")
    return float((payload.get("format") or {}).get("duration") or 0.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="音画同步检查（时长 + 片头偏移）")
    parser.add_argument("--video", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--preroll", type=float, default=0.0, help="负时间片头时长（秒）")
    parser.add_argument("--tolerance", type=float, default=MAX_OFFSET_SEC)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = check_sync(
            probe_duration(args.video),
            probe_duration(args.audio),
            preroll_sec=args.preroll,
            tolerance_sec=args.tolerance,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 边界
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json else report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
