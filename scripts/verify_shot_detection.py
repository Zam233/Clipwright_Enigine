"""R2 验收：镜头切分质量（precision / recall / F1）+ 已知失效模式。

计划 §4 的目标是"镜头切分 F1 ≥ 0.90"。本机没有真实素材（计划 §6 决定 4），
因此用**合成素材 + 精确已知的切点**做基准——好处是 ground truth 绝对干净，
坏处是分布比真实拍摄简单（这一点在最后如实列出）。

三组：
- **easy**：纯色硬切（scene 检测最容易，作为 baseline 与门槛）
- **texture**：纹理素材硬切（testsrc2 花样不同，真实一点的画面变化）
- **hard**（不设门槛，只报告失效模式）：
  · `xfade` 交叉溶解——**渐变转场不是硬切**，scene 检测看不到，会被漏掉；
  · 同色相邻段——画面完全相同，**任何基于像素的检测都不可能**发现；
  · 段内亮度突跳——自动曝光/闪光会被**误报**成切点。

用法：
    python scripts/verify_shot_detection.py [--threshold 0.3] [--tol 0.25] [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.services import content_index as ci  # noqa: E402

#: 颜色调色板（亮度/色相差异明显，保证 scene 分数足够高）
PALETTE = ["red", "blue", "green", "white", "black", "orange", "purple", "#1a3a7a", "#f0e68c"]
FPS = 10


@dataclass
class Case:
    name: str
    path: Path
    truth: list[float]
    group: str = "easy"
    notes: str = ""


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg 失败: {r.stderr[-400:]}")


def _concat_sources(path: Path, sources: list[str]) -> None:
    """把若干 lavfi 源按给定顺序首尾拼接（每段时长用 `-t` 指定，兼容所有源）。"""
    args: list[str] = []
    durations: list[float] = []
    for src in sources:
        spec, _, dur = src.partition("|")
        args += ["-f", "lavfi", "-t", dur, "-i", spec]
        durations.append(float(dur))
    chain = "".join(f"[{i}:v]" for i in range(len(sources)))
    chain += f"concat=n={len(sources)}:v=1:a=0[out]"
    _run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", chain,
          "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
          "-pix_fmt", "yuv420p", str(path)])


def _boundaries(durations: list[float]) -> list[float]:
    cuts, acc = [], 0.0
    for d in durations[:-1]:
        acc += d
        cuts.append(round(acc, 3))
    return cuts


def make_solid_cuts(path: Path, durations: list[float]) -> list[float]:
    """纯色段硬切；返回切点（段边界，不含 0 与总时长）。"""
    _concat_sources(path, [
        f"color=c={PALETTE[i % len(PALETTE)]}:s=160x120:r={FPS}|{d}"
        for i, d in enumerate(durations)
    ])
    return _boundaries(durations)


def make_texture_cuts(path: Path, durations: list[float]) -> list[float]:
    """纹理段硬切：让人工合成素材更像真实拍摄的帧间差异。"""
    specs = [
        f"testsrc2=s=160x120:r={FPS}",
        f"smptebars=s=160x120:r={FPS}",
        f"testsrc2=s=120x90:r={FPS},scale=160:120",
        f"rgbtestsrc=s=160x120:r={FPS}",
    ]
    _concat_sources(path, [f"{specs[i % len(specs)]}|{d}" for i, d in enumerate(durations)])
    return _boundaries(durations)


def make_crossfade(path: Path, seg: float = 1.5, fade: float = 0.5) -> list[float]:
    """交叉溶解：转场中点算作切点（对硬切检测而言属于"不可见"）。"""
    _run(["ffmpeg", "-y", "-loglevel", "error",
          "-f", "lavfi", "-i", f"color=c=red:s=160x120:r={FPS}:d={seg}",
          "-f", "lavfi", "-i", f"color=c=blue:s=160x120:r={FPS}:d={seg}",
          "-filter_complex",
          f"[0:v][1:v]xfade=transition=fade:duration={fade}:offset={seg - fade}[out]",
          "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
          "-pix_fmt", "yuv420p", str(path)])
    return [round(seg - fade + fade / 2, 3)]


def make_same_color_cuts(path: Path, durations: list[float]) -> list[float]:
    """同色相邻段：切点存在但画面完全一致（任何像素方法都测不到）。"""
    _concat_sources(path, [f"color=c=#123456:s=160x120:r={FPS}|{d}" for d in durations])
    return _boundaries(durations)


def make_brightness_step(path: Path, seg: float = 3.0, step_at: float = 1.5) -> list[float]:
    """段内亮度突跳（闪光/自动曝光）——**没有切点**，用来量误报。"""
    _run(["ffmpeg", "-y", "-loglevel", "error",
          "-f", "lavfi", "-i", f"testsrc2=s=160x120:r={FPS}:d={seg}",
          "-vf", f"eq=brightness=if(gte(t\\,{step_at})\\,0.55\\,-0.35)",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)])
    return []


def match_cuts(detected: list[float], truth: list[float], tol: float) -> tuple[int, int, int]:
    """贪心一对一匹配 → (命中, 误报, 漏报)。"""
    det = sorted(detected)
    tru = sorted(truth)
    used_det: set[int] = set()
    hits = 0
    for t in tru:
        best, best_dist = None, None
        for i, d in enumerate(det):
            if i in used_det:
                continue
            dist = abs(d - t)
            if dist <= tol and (best_dist is None or dist < best_dist):
                best, best_dist = i, dist
        if best is not None:
            used_det.add(best)
            hits += 1
    return hits, len(det) - len(used_det), len(tru) - hits


def prf(hits: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = hits / (hits + fp) if (hits + fp) else 1.0
    r = hits / (hits + fn) if (hits + fn) else 1.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--tol", type=float, default=0.25, help="切点匹配容差（秒）")
    ap.add_argument("--min-f1", type=float, default=0.90, help="easy/texture 组的 F1 门槛")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("需要本机 ffmpeg/ffprobe")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="clipwright_shotdet_"))
    cases: list[Case] = []
    try:
        # easy：4 段纯色硬切 ×3 组不同节奏
        for i, durs in enumerate([
            [1.2, 1.0, 1.5, 1.0],
            [0.8, 2.0, 0.9, 1.3],
            [2.0, 0.7, 0.8, 1.9],
        ]):
            p = tmp / f"easy_{i}.mp4"
            cases.append(Case(f"easy_{i}", p, make_solid_cuts(p, durs), "easy"))
        # texture：4 段纹理硬切
        for i, durs in enumerate([[1.0, 1.0, 1.0, 1.0], [1.4, 0.9, 1.6, 1.1]]):
            p = tmp / f"texture_{i}.mp4"
            cases.append(Case(f"texture_{i}", p, make_texture_cuts(p, durs), "texture"))
        # hard（不设门槛）
        p = tmp / "hard_crossfade.mp4"
        cases.append(Case("hard_crossfade", p, make_crossfade(p), "hard",
                          "渐变转场不是硬切"))
        p = tmp / "hard_same_color.mp4"
        cases.append(Case("hard_same_color", p, make_same_color_cuts(p, [1.5, 1.5]), "hard",
                          "同色相邻段：像素无差异"))
        p = tmp / "hard_brightness_step.mp4"
        cases.append(Case("hard_brightness_step", p, make_brightness_step(p), "hard",
                          "段内亮度突跳：误报来源"))

        print(f"合成 {len(cases)} 段测试素材（切点容差 ±{args.tol}s，阈值 {args.threshold}）\n")
        header = f"{'case':<22}{'真值':>5}{'检出':>5}{'命中':>5}{'误报':>5}{'漏报':>5}{'P':>8}{'R':>8}{'F1':>8}"
        print(header)
        print("-" * len(header))
        bucket: dict[str, list[int]] = {}
        for c in cases:
            detected = await ci.detect_cut_times(c.path, args.threshold)
            ranges = ci.build_shot_ranges(detected, _duration(c.path))
            hits, fp, fn = match_cuts(detected, c.truth, args.tol)
            p, r, f1 = prf(hits, fp, fn)
            print(f"{c.name:<22}{len(c.truth):>5}{len(detected):>5}{hits:>5}{fp:>5}{fn:>5}"
                  f"{p:>8.3f}{r:>8.3f}{f1:>8.3f}"
                  + (f"   ← {c.notes}" if c.notes else ""))
            if c.group in ("easy", "texture"):
                b = bucket.setdefault(c.group, [0, 0, 0, 0])  # hits, fp, fn, shots
                b[0] += hits
                b[1] += fp
                b[2] += fn
                b[3] += len(ranges)
            else:
                b = bucket.setdefault("hard", [0, 0, 0, 0])
                b[0] += hits
                b[1] += fp
                b[2] += fn
                b[3] += len(ranges)

        print()
        failures: list[str] = []
        for group in ("easy", "texture", "hard"):
            if group not in bucket:
                continue
            hits, fp, fn, _shots = bucket[group]
            p, r, f1 = prf(hits, fp, fn)
            tag = "（不设门槛，仅报告）" if group == "hard" else ""
            print(f"{group:<8} P={p:.3f} R={r:.3f} F1={f1:.3f} {tag}")
            if group != "hard" and f1 < args.min_f1:
                failures.append(f"{group} 组 F1={f1:.3f} < {args.min_f1}")

        print("\n失效模式观察（按实测结果写，不预设结论）：")
        by_name = {c.name: c for c in cases}
        for c in cases:
            if c.group != "hard":
                continue
            detected = await ci.detect_cut_times(c.path, args.threshold)
            hits, fp, fn = match_cuts(detected, c.truth, args.tol)
            verdict = []
            if c.truth and hits < len(c.truth):
                verdict.append(f"漏报 {len(c.truth) - hits}/{len(c.truth)}")
            if fp:
                verdict.append(f"误报 {fp}")
            if not verdict:
                verdict.append("本组参数下未复现问题")
            print(f"  · {c.name}（{c.notes}）：检测到 {len(detected)} 个切点 → " + "、".join(verdict))
        print("  · 结论：渐变转场与同色相邻段属**方法学盲区**（前者不是硬切、后者像素无差异），"
              "需靠其它信号（音频事件/语义层）补；亮度突跳是否误报取决于幅度与阈值，"
              "不可当作必然，应在真实素材上量。")

        print()
        if failures:
            print("SHOT DETECTION VERIFY FAIL")
            for f in failures:
                print("  -", f)
            return 1
        print("SHOT DETECTION VERIFY PASS")
        return 0
    finally:
        if args.keep:
            print(f"[keep] {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def _duration(path: Path) -> float:
    """ffprobe 取时长（同步；脚本内部用）。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
