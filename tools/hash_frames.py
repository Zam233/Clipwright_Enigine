"""逐帧哈希比对（PRD C6 / R-03，验收 V22）。

用途：证明"同一工程两次渲染逐帧相同"。做法是最笨最可信的一种——把视频解成
``rgb24`` 原始帧，逐帧 sha256，再逐帧比对；任何一帧不同都会定位到帧号。

用法::

    python -m tools.hash_frames a.mp4 b.mp4            # 两段对比
    python -m tools.hash_frames out/a out/b            # 两个 PNG 目录对比
    python -m tools.hash_frames a.mp4 --hash-only      # 只打印帧哈希清单

退出码：0 = 完全一致；1 = 有差异或出错。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


@dataclass
class CompareResult:
    total_a: int
    total_b: int
    first_diff: int | None
    diffs: list[int]

    @property
    def equal(self) -> bool:
        return self.total_a == self.total_b and not self.diffs

    def summary(self) -> str:
        if self.equal:
            return f"逐帧一致：{self.total_a} 帧"
        parts = [f"A={self.total_a} 帧，B={self.total_b} 帧"]
        if self.first_diff is not None:
            parts.append(f"首个差异帧 #{self.first_diff}")
        parts.append(f"差异共 {len(self.diffs)} 帧")
        return "；".join(parts)


def hash_frames_from_raw(data: bytes, frame_size: int) -> list[str]:
    """把 rgb24 原始字节流切成帧并逐帧 sha256。"""
    if frame_size <= 0:
        raise ValueError("frame_size 必须为正")
    digests: list[str] = []
    for offset in range(0, len(data) - frame_size + 1, frame_size):
        chunk = data[offset : offset + frame_size]
        digests.append(hashlib.sha256(chunk).hexdigest())
    return digests


def _probe_size(path: Path) -> tuple[int, int]:
    """读视频宽高（ffprobe 优先，失败则回退 1920x1080 会由调用方覆盖）。"""
    ffprobe = _which("ffprobe")
    if ffprobe is None:
        return 0, 0
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        payload = json.loads(out.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def hash_video(path: str | Path, width: int = 0, height: int = 0) -> list[str]:
    """解出视频逐帧哈希（rgb24）。宽高未知时用 ffprobe 探测。"""
    video = Path(path)
    if not video.exists():
        raise FileNotFoundError(video)
    if not (width and height):
        width, height = _probe_size(video)
    if not (width and height):
        raise RuntimeError(f"无法确定 {video} 的宽高，请显式传 --width/--height")
    ffmpeg = _which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("未找到 ffmpeg")
    proc = subprocess.run(
        [
            ffmpeg, "-v", "error", "-i", str(video),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 解码失败：{proc.stderr.decode('utf-8', 'replace')[:400]}"
        )
    return hash_frames_from_raw(proc.stdout, width * height * 3)


def hash_png_dir(path: str | Path) -> list[str]:
    """按文件名稳定排序后逐张 PNG 的字节算 sha256。"""
    directory = Path(path)
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    digests: list[str] = []
    for frame in sorted(directory.glob("*.png")):
        digests.append(hashlib.sha256(frame.read_bytes()).hexdigest())
    return digests


def compare(a: Sequence[str], b: Sequence[str]) -> CompareResult:
    diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(a) != len(b):
        # 长度不同也算差异；把多出来的帧号列进去
        longer = max(len(a), len(b))
        for i in range(min(len(a), len(b)), longer):
            diffs.append(i)
    first = diffs[0] if diffs else None
    return CompareResult(len(a), len(b), first, diffs)


def _load(target: str | Path, width: int, height: int) -> list[str]:
    path = Path(target)
    if path.is_dir():
        return hash_png_dir(path)
    return hash_video(path, width, height)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐帧哈希比对（确定性验收）")
    parser.add_argument("targets", nargs="+", help="两段视频或两个 PNG 目录")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if len(args.targets) == 1:
        digests = _load(args.targets[0], args.width, args.height)
        if args.json:
            print(json.dumps({"frames": len(digests), "sha256": digests}, indent=2))
        else:
            print(f"{len(digests)} 帧")
            for index, digest in enumerate(digests):
                print(f"{index}\t{digest}")
        return 0

    if len(args.targets) != 2:
        parser.error("需要 1 个（只看哈希）或 2 个（比对）目标")

    try:
        a = _load(args.targets[0], args.width, args.height)
        b = _load(args.targets[1], args.width, args.height)
    except Exception as exc:  # noqa: BLE001 - CLI 需要把错误变成退出码
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    result = compare(a, b)
    if args.json:
        print(
            json.dumps(
                {
                    "equal": result.equal,
                    "total_a": result.total_a,
                    "total_b": result.total_b,
                    "first_diff": result.first_diff,
                    "diffs": result.diffs[:50],
                },
                indent=2,
            )
        )
    else:
        print(result.summary())
        if result.diffs:
            print("差异帧（前 20）：", ", ".join(str(i) for i in result.diffs[:20]))
    return 0 if result.equal else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
