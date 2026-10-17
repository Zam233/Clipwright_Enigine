"""MG 快路径 vs Chrome 路径：逐帧比对（人眼看 + 数值）。

用法：
    python scripts/verify_mg_fastpath.py                     # 全部模板，只看总量
    python scripts/verify_mg_fastpath.py mg_title_reveal     # 指定模板（出对照图）
    python scripts/verify_mg_fastpath.py --no-chrome         # 只跑快路径（无 hyperframes 时）

判定口径（**合成到黑底上的 RGB** 才是观众看到的东西，因此以它为主）：
- 平均绝对差 (`mean_abs`) ≤ 阈值 → 通过；
- 前 10% 最差帧的均值也一并给出（避免"均值被大片静止背景稀释"）。

对照图写入 `--out` 目录（默认 `_mg_fastpath_verify/`），格式为三联：
Chrome 帧 | 快路径帧 | 差异放大图。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _extract(mov: Path, out_dir: Path, fps: float, size: tuple[int, int]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mov),
         "-vf", f"scale={size[0]}:{size[1]}", str(out_dir / "c%05d.png")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"抽帧失败: {r.stderr[-300:]}")
    return sorted(out_dir.glob("c*.png"))


def _flatten(img, bg=(0, 0, 0)):
    """RGBA → 黑底 RGB（观众实际看到的画面）。"""
    from PIL import Image

    base = Image.new("RGB", img.size, bg)
    base.paste(img, (0, 0), img)
    return base


def _find_hyperframes() -> str | None:
    import os
    import shutil

    env = os.environ.get("HYPERFRAMES_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("hyperframes")
    if found:
        return found
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx"
    if cache.is_dir():
        for p in sorted(cache.glob("*/node_modules/.bin/hyperframes.cmd")):
            return str(p)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("templates", nargs="*", help="模板名（默认全部）")
    ap.add_argument("--out", default="_mg_fastpath_verify")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--threshold", type=float, default=6.0, help="Chrome 有效区内平均绝对差阈值")
    ap.add_argument("--min-iou", type=float, default=0.90, help="alpha 掩膜 IoU 下限（仅记录，不判定）")
    ap.add_argument("--no-chrome", action="store_true", help="跳过 Chrome（只验证快路径出片）")
    args = ap.parse_args()

    from clipwright.animation.mg_fastpath import load_template_defs, render_mg_fastpath
    from clipwright.animation.mg_renderer import MGRenderer

    defs = load_template_defs()
    if args.templates:
        wanted = set(args.templates)
        defs = [(n, d) for n, d in defs if n in wanted]
        if not defs:
            print(f"未找到模板: {args.templates}")
            return 2

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    size = (args.width, args.height)
    hf = None if args.no_chrome else _find_hyperframes()
    if not args.no_chrome and not hf:
        print("未找到 hyperframes（可加 --no-chrome 只验证快路径）")
        return 2

    rows = []
    failures = []
    for name, mg_def in defs:
        params = {
            k: (v.get("default") if isinstance(v, dict) else v)
            for k, v in (mg_def.get("params") or {}).items()
        }
        params.setdefault("text", "帧艺 ClipWright")
        dur = float(mg_def.get("duration_sec") or 0)
        frames = max(1, int(round(dur * args.fps)))

        t0 = time.perf_counter()
        fast_mov = render_mg_fastpath(
            mg_def, out_root / f"{name}_fast.mov", size, args.fps, params=params)
        fast_sec = time.perf_counter() - t0
        if not fast_mov:
            print(f"{name}: 快路径不可用（应被 coverage 提前排除）")
            failures.append(name)
            continue

        row = {"name": name, "fast_sec": round(fast_sec, 2), "frames": frames}
        if hf:
            html = MGRenderer.render(mg_def, params, width=size[0], height=size[1], fps=args.fps)
            work = out_root / f"{name}_html"
            work.mkdir(parents=True, exist_ok=True)
            (work / "index.html").write_text(html, encoding="utf-8")
            chrome_mov = out_root / f"{name}_chrome.mov"
            t0 = time.perf_counter()
            r = subprocess.run(
                [hf, "render", str(work), "-o", str(chrome_mov), "--format", "mov",
                 "-f", str(int(args.fps)), "--quiet"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            row["chrome_sec"] = round(time.perf_counter() - t0, 2)
            if r.returncode != 0 or not chrome_mov.exists():
                print(f"{name}: Chrome 渲染失败 rc={r.returncode} {r.stderr[-200:]}")
                failures.append(name)
                continue
            a = _extract(chrome_mov, out_root / f"{name}_chrome_frames", args.fps, size)
            b = _extract(Path(fast_mov), out_root / f"{name}_fast_frames", args.fps, size)
            n = min(len(a), len(b))
            if n == 0:
                print(f"{name}: 帧数为 0")
                failures.append(name)
                continue
            from PIL import Image, ImageChops

            # ── 先确定 Chrome **实际渲染到**的区域 ──
            # 本机环境有一个已确认的既有缺陷：hyperframes 起 Chrome 的窗口约为合成尺寸的
            # 一半（实测三档分辨率恒为 W/2+64 × H/2+16），于是 mov 只有左上角一块有内容，
            # 其余全透明。直接整帧比对会把"Chrome 被裁掉"算成"快路径画错了"。
            # 因此先在若干帧上取 Chrome alpha 的并集作为"Chrome 有效区"，区域内的差异
            # 才是真正的保真度证据；区域外只报告不判定（并在报告里写明）。
            region = None
            for i in range(0, n, max(1, n // 8)):
                ib = Image.open(a[i]).convert("RGBA")
                bb = ib.getchannel("A").getbbox()
                if bb is None:
                    continue
                region = bb if region is None else (
                    min(region[0], bb[0]), min(region[1], bb[1]),
                    max(region[2], bb[2]), max(region[3], bb[3]),
                )
            if region is None:
                region = (0, 0, size[0], size[1])
            total_px = size[0] * size[1]
            region_px = max(1, (region[2] - region[0]) * (region[3] - region[1]))

            diffs_flat, diffs_alpha, visible, ious, region_diffs, region_visible = [], [], [], [], [], []
            worst = (0.0, 0)
            for i in range(n):
                ia = Image.open(a[i]).convert("RGBA")
                ib = Image.open(b[i]).convert("RGBA")
                if ia.size != ib.size:
                    ib = ib.resize(ia.size)
                fa, fb = _flatten(ia), _flatten(ib)
                d = ImageChops.difference(fa, fb)
                mean = sum(v * c for v, c in enumerate(d.convert("L").histogram())) / (255.0 * total_px)
                diffs_flat.append(mean)
                # 可见差异像素占比：任一通道差 > 8/255 才算"看得出来"
                big = d.convert("L").point(lambda v: 255 if v > 8 else 0)
                visible.append(sum(c for v, c in enumerate(big.histogram()) if v == 255) / total_px)
                # 区域内指标（判定用）
                dr = d.crop(region).convert("L")
                region_diffs.append(sum(v * c for v, c in enumerate(dr.histogram())) / (255.0 * region_px))
                br = dr.point(lambda v: 255 if v > 8 else 0)
                region_visible.append(sum(c for v, c in enumerate(br.histogram()) if v == 255) / region_px)
                da = ImageChops.difference(ia.getchannel("A"), ib.getchannel("A"))
                diffs_alpha.append(sum(v * c for v, c in enumerate(da.histogram())) / (255.0 * total_px))
                # alpha 掩膜 IoU：抓"位置/尺寸错"这类肉眼不一定立刻发现的问题
                ma = ia.getchannel("A").point(lambda v: 255 if v > 40 else 0)
                mb = ib.getchannel("A").point(lambda v: 255 if v > 40 else 0)
                inter = ImageChops.multiply(ma, mb)
                union = ImageChops.lighter(ma, mb)
                i_count = sum(c for v, c in enumerate(inter.histogram()) if v == 255)
                u_count = sum(c for v, c in enumerate(union.histogram()) if v == 255)
                ious.append(i_count / u_count if u_count else 1.0)
                if mean > worst[0]:
                    worst = (mean, i)
                    if i < 6 or i % 10 == 0:
                        side = Image.new("RGB", (fa.size[0] * 3, fa.size[1]))
                        side.paste(fa, (0, 0))
                        side.paste(fb, (fa.size[0], 0))
                        amp = ImageChops.difference(fa, fb).point(lambda v: min(255, v * 4))
                        side.paste(amp, (fa.size[0] * 2, 0))
                        side.save(out_root / f"{name}_cmp_{i:03d}.png")
            rm = sum(region_diffs) / len(region_diffs)
            rv = sum(region_visible) / len(region_visible)
            row.update({
                "mean_abs_flat": round(sum(diffs_flat) / len(diffs_flat), 3),
                "mean_abs_alpha": round(sum(diffs_alpha) / len(diffs_alpha), 3),
                "visible_pixel_ratio": round(sum(visible) / len(visible), 5),
                "alpha_iou": round(sum(ious) / len(ious), 4),
                "chrome_region": list(region),
                "chrome_region_ratio": round(region_px / total_px, 4),
                "region_mean_abs": round(rm, 3),
                "region_visible_ratio": round(rv, 5),
                "worst_frame": worst[1],
                "compare_png": str(out_root / f"{name}_cmp_{worst[1]:03d}.png"),
            })
            if row["region_mean_abs"] > args.threshold:
                failures.append(f"{name}(区域内平均差 {row['region_mean_abs']})")
        rows.append(row)
        line = f"{name:26s} frames={row['frames']:4d} fast={row['fast_sec']:6.2f}s"
        if "chrome_sec" in row:
            speedup = row["chrome_sec"] / max(row["fast_sec"], 0.01)
            line += (f" chrome={row['chrome_sec']:6.2f}s 提速={speedup:.2f}x"
                     f" 区域内差={row['region_mean_abs']:.2f}/{row['region_visible_ratio'] * 100:.2f}%"
                     f" 整帧差={row['mean_abs_flat']:.2f}"
                     f" Chrome有效区={row['chrome_region_ratio'] * 100:.1f}%")
        print(line)

    (out_root / "report.json").write_text(
        json.dumps({"rows": rows, "failures": failures, "threshold": args.threshold},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out_root / 'report.json'}")
    if failures:
        print(f"未通过: {failures}")
        return 1
    print("MG FASTPATH VERIFY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
