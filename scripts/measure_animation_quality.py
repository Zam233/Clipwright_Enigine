"""动画质量量化工具（PRD 验收用）。

两个子命令：

1. `motion`：读 timeline artifact，统计每段动画的「动效强度」——
   最大位移占画布宽度百分比、缩放极差、首次运动时间、静止尾部占比。
   用来回答「为什么逐帧哈希显示在动，人眼却觉得是静止的」。

2. `contrast`：读成片抽帧，在指定区域里估算「文字像素 vs 背景」的 WCAG 对比度。
   用来回答「背景一变就看不清」。

示例：

    python scripts/measure_animation_quality.py motion \
        --timeline workspaces/proj_x/artifacts/art_llm_anim.json

    python scripts/measure_animation_quality.py contrast \
        --frame renders/final.png --region 0.56,0.33,0.86,0.42 --label "对比右栏"
"""

from __future__ import annotations

import argparse
import json
import statistics as stats
from pathlib import Path
from typing import Any

#: 动效强度参考阈值（PRD 首轮建议值，按 fixture 校准）
MIN_SHIFT_PCT = 4.0
MAX_STATIC_TAIL = 0.35
MIN_CONTRAST = 4.5


def _elements(mg_def: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (mg_def.get("elements") or [])
            if isinstance(e, dict) and str(e.get("type")) != "bg"]


def analyze_mg_def(mg_def: dict[str, Any], width: int = 1920) -> dict[str, Any]:
    dur = float(mg_def.get("duration_sec") or 0) or 1.0
    max_shift = 0.0
    max_scale = 0.0
    first_move: float | None = None
    last_move = 0.0
    moving = 0
    for elem in _elements(mg_def):
        kfs = [k for k in (elem.get("keyframes") or []) if isinstance(k, dict)]
        if len(kfs) < 2:
            continue
        moved = False
        for key in ("translate_x", "translate_y", "x", "y"):
            vals = [float(k.get(key)) for k in kfs if isinstance(k.get(key), (int, float))]
            if len(vals) >= 2:
                span = max(vals) - min(vals)
                if span > 0:
                    moved = True
                    max_shift = max(max_shift, span)
                    times = [float(k.get("time") or 0) for k in kfs]
                    first_move = times[0] if first_move is None else min(first_move, times[0])
                    last_move = max(last_move, times[-1])
        scale_vals = [float(k.get("scale")) for k in kfs if isinstance(k.get("scale"), (int, float))]
        if len(scale_vals) >= 2:
            span = max(scale_vals) - min(scale_vals)
            max_scale = max(max_scale, span)
            if span > 0:
                moved = True
        if moved:
            moving += 1
    return {
        "elements": len(_elements(mg_def)),
        "moving_elements": moving,
        "max_shift_px": round(max_shift, 1),
        "max_shift_pct": round(max_shift / width * 100, 2),
        "max_scale_delta": round(max_scale, 3),
        "first_move_at": first_move,
        "last_move_at": round(last_move, 2),
        "duration_sec": round(dur, 2),
        "static_tail_ratio": round(max(0.0, dur - last_move) / dur, 2),
    }


def cmd_motion(args: argparse.Namespace) -> dict[str, Any]:
    data = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    width = int(data.get("width") or 1920)
    rows: list[dict[str, Any]] = []
    for track in data.get("tracks") or []:
        if str(track.get("kind")) != "animation":
            continue
        for clip in track.get("clips") or []:
            mg = (clip.get("metadata") or {}).get("mg_def") or {}
            if not isinstance(mg, dict) or not mg:
                continue
            row = analyze_mg_def(mg, width)
            row.update({
                "start_sec": round(float(clip.get("start_sec") or 0), 2),
                "type": (clip.get("metadata") or {}).get("anim_type"),
                "renderer": (clip.get("metadata") or {}).get("renderer"),
            })
            rows.append(row)

    shifts = [r["max_shift_pct"] for r in rows]
    tails = [r["static_tail_ratio"] for r in rows]
    report = {
        "timeline": args.timeline,
        "clips": len(rows),
        "thresholds": {
            "min_shift_pct": MIN_SHIFT_PCT,
            "max_static_tail_ratio": MAX_STATIC_TAIL,
        },
        "summary": {
            "median_shift_pct": round(stats.median(shifts), 2) if shifts else 0.0,
            "min_shift_pct": min(shifts) if shifts else 0.0,
            "max_shift_pct": max(shifts) if shifts else 0.0,
            "below_threshold": sum(1 for s in shifts if s < MIN_SHIFT_PCT),
            "static_tail_violations": sum(1 for t in tails if t > MAX_STATIC_TAIL),
            "median_elements": stats.median([r["elements"] for r in rows]) if rows else 0,
        },
        "clips_detail": sorted(rows, key=lambda r: r["max_shift_pct"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _srgb_to_lin(v: float) -> float:
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rel_lum(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_srgb_to_lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def cmd_contrast(args: argparse.Namespace) -> dict[str, Any]:
    from PIL import Image

    img = Image.open(args.frame).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = (float(v) for v in args.region.split(","))
    crop = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    px = list(crop.getdata())
    lum = sorted((_rel_lum(p) for p in px))
    n = len(lum)
    if n == 0:
        raise SystemExit("空区域")
    text_l = sum(lum[int(n * 0.90):]) / max(1, n - int(n * 0.90))
    bg_slice = lum[int(n * 0.05):int(n * 0.70)] or lum
    bg_l = stats.median(bg_slice)
    ratio = _contrast(text_l, bg_l)
    report = {
        "frame": args.frame,
        "label": args.label or "",
        "region": args.region,
        "text_luminance": round(text_l, 3),
        "background_luminance": round(bg_l, 3),
        "contrast_ratio": round(ratio, 2),
        "threshold": MIN_CONTRAST,
        "pass": ratio >= MIN_CONTRAST,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="动画质量量化（motion / contrast）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("motion", help="统计每段动画的动效强度")
    m.add_argument("--timeline", required=True)
    m.add_argument("--out")
    m.set_defaults(func=cmd_motion)

    c = sub.add_parser("contrast", help="估算文字与背景的 WCAG 对比度")
    c.add_argument("--frame", required=True)
    c.add_argument("--region", required=True, help="归一化区域 x0,y0,x1,y1")
    c.add_argument("--label", default="")
    c.add_argument("--out")
    c.set_defaults(func=cmd_contrast)

    args = ap.parse_args()
    report = args.func(args)
    bad = False
    if args.cmd == "motion":
        bad = report["summary"]["below_threshold"] > 0
    else:
        bad = not report["pass"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
