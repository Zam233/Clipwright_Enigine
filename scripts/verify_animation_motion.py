"""动画动效验收脚本（M4）。

用途：给定一个 timeline artifact（或直接给定若干 MOV），逐段抽帧计算
「不同帧占比」，判定动画是否真的在动。

为什么需要：改造前 45 个静态 SVG 片段在 6 秒里只有 2 个不同帧（一帧空白 +
一帧静止），却没有任何检查会拦下它们——渲染照样报 success。这个脚本把
"必须动"变成可复现的数字。

用法（后端仓库根目录）：

    python scripts/verify_animation_motion.py --timeline <artifact.json> --out report.json
    python scripts/verify_animation_motion.py --mov renders/anim_0.mov --label batch0

判定：4–6 秒片段 ≥ 8 个不同采样帧且 distinct_ratio ≥ 0.25 视为通过。
退出码：全部通过 0；存在不达标 1。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

#: 采样帧率（每秒抽 4 帧足够判定「在动」，又不至于太慢）
SAMPLE_FPS = 4
#: 通过门槛
MIN_DISTINCT_FRAMES = 8
MIN_DISTINCT_RATIO = 0.25


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=False, timeout=1800)


def _ffprobe_duration(path: str) -> float:
    r = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path,
    ])
    try:
        return float(r.stdout.decode().strip())
    except Exception:  # noqa: BLE001
        return 0.0


def frame_hashes(path: str, fps: int = SAMPLE_FPS) -> list[str]:
    """按 `fps` 抽帧并返回逐帧哈希（用 framemd5，避免解码成图片）。"""
    r = _run(["ffmpeg", "-v", "error", "-i", path,
              "-vf", f"fps={fps}", "-f", "framemd5", "-"])
    if r.returncode != 0:
        return []
    hashes: list[str] = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            hashes.append(parts[-1])
    return hashes


def analyze(path: str, label: str | None = None) -> dict[str, Any]:
    duration = _ffprobe_duration(path)
    hashes = frame_hashes(path)
    distinct = len(set(hashes))
    ratio = round(distinct / max(1, len(hashes)), 3)
    enough = distinct >= MIN_DISTINCT_FRAMES and ratio >= MIN_DISTINCT_RATIO
    return {
        "label": label or Path(path).stem,
        "path": path,
        "duration_sec": round(duration, 2),
        "sampled_frames": len(hashes),
        "distinct_frames": distinct,
        "distinct_ratio": ratio,
        "ok": enough,
    }


def _timeline_animation_clips(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = artifact.get("tracks") or []
    out: list[dict[str, Any]] = []
    for track in tracks:
        if str(track.get("kind")) != "animation":
            continue
        for clip in track.get("clips") or []:
            out.append(clip)
    return out


def summarize_timeline(artifact_path: str) -> dict[str, Any]:
    """统计一个 timeline 里动画的类型分布与「LLM 化」程度（不渲染）。"""
    data = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    clips = _timeline_animation_clips(data)
    by_type: dict[str, int] = {}
    llm_clips = 0
    static_clips = 0
    dup: dict[str, int] = {}
    for clip in clips:
        meta = clip.get("metadata") or {}
        type_id = str(meta.get("anim_type") or "")
        by_type[type_id] = by_type.get(type_id, 0) + 1
        if str(meta.get("renderer")) == "mg_hyperframes":
            llm_clips += 1
        if meta.get("diagram_params"):
            static_clips += 1
        key = f"{type_id}|{str(clip.get('text'))[:40]}"
        dup[key] = dup.get(key, 0) + 1
    total = len(clips)
    repeats = sum(v - 1 for v in dup.values() if v > 1)
    return {
        "artifact": artifact_path,
        "animation_clips": total,
        "llm_generated": llm_clips,
        "svg_fallback_or_legacy": static_clips,
        "llm_ratio": round(llm_clips / max(1, total), 3),
        "duplicate_clips": repeats,
        "by_type": by_type,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="动画动效验收（逐帧哈希多样性）")
    ap.add_argument("--timeline", help="timeline artifact JSON（统计 LLM 化程度）")
    ap.add_argument("--mov", action="append", default=[], help="待验收的 MOV/MP4（可多次）")
    ap.add_argument("--label", help="单文件时的标签")
    ap.add_argument("--out", help="报告输出路径（JSON）")
    args = ap.parse_args(argv)

    report: dict[str, Any] = {"threshold": {
        "sample_fps": SAMPLE_FPS,
        "min_distinct_frames": MIN_DISTINCT_FRAMES,
        "min_distinct_ratio": MIN_DISTINCT_RATIO,
    }}

    if args.timeline:
        report["timeline"] = summarize_timeline(args.timeline)

    results = []
    for i, path in enumerate(args.mov):
        if not Path(path).exists():
            results.append({"label": Path(path).stem, "path": path, "ok": False,
                            "error": "文件不存在"})
            continue
        results.append(analyze(path, args.label if len(args.mov) == 1 else None))
    if results:
        report["motion"] = results
        report["all_ok"] = all(r.get("ok") for r in results)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n报告已写入 {args.out}", file=sys.stderr)

    if "all_ok" in report and not report["all_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
