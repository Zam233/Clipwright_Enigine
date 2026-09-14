"""验证 Chrome 路径下 MG 全幅背景的**覆盖完整性**（回归守卫）。

背景（V3 验证时发现的真实缺陷）
------------------------------
`MGRenderer` 旧实现把 center 锚点的 `translateX(-50%) translateY(-50%)` 一并写进
**所有**元素的关键帧——包括 `bg` 全幅底层。`bg` 是 `inset:0` 的全幅层，本不该有
此位移，于是它被平移自身尺寸的一半，**只剩左上四分之一可见**：

    1920×1080 合成 → 背景只覆盖 960×540（实测 alpha 覆盖 25%）
    1280×720      → 640×360
    640×480       → 320×240

这一现象最初被误判为"hyperframes 把截图裁掉了 53%"（因为测到的"有效区"恰好是
`W/2+64·dpr × H/2+16·dpr`——其实那是"被平移的背景 + 其它元素露出的边"的并集，
那 64/16 就是其它元素超出四分之一区域的部分）。逐项排查证明**不存在截图裁剪**：
换捕获后端 / low-memory / GPU / workers / page-side 合成 / resolution 预设（1×、2×）/
系统 Chrome / 给 `<html>` 补 data-resolution 与 #stage，alpha 覆盖区都一模一样。

本脚本渲染一份"全幅背景 + 居中文本"的最小 MG，量它的 alpha 覆盖：
**必须接近整帧**；只有四分之一即判定失败。修好之后，这一条同时是
`verify_mg_fastpath.py`（快路径 vs Chrome）能给出有意义差分的前提。

用法：
    python scripts/verify_mg_bg_coverage.py [--width 1920] [--height 1080]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 覆盖率的合格线（留出抗锯齿/编码损失）
MIN_RATIO = 0.97


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


def _probe_def(width: int, height: int) -> dict:
    return {
        "animation_id": "bg_coverage_probe",
        "duration_sec": 1.0,
        "elements": [
            {"type": "bg", "background": "#123456",
             "keyframes": [{"time": 0, "opacity": 1}, {"time": 1.0, "opacity": 1}]},
            {"type": "text", "content": "COVERAGE", "x": "center", "y": "center",
             "font_size": 40, "font_color": "#ffffff",
             "keyframes": [{"time": 0, "opacity": 1}, {"time": 1.0, "opacity": 1}]},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--out", default="_mg_bg_coverage")
    args = ap.parse_args()

    import shutil

    from clipwright.animation.mg_renderer import MGRenderer

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("需要本机 ffmpeg/ffprobe")
        return 2
    hf = _find_hyperframes()
    if not hf:
        print("未找到 hyperframes CLI（本脚本验证的是 Chrome 路径）")
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mg = _probe_def(args.width, args.height)
    html = MGRenderer.render(mg, {}, width=args.width, height=args.height, fps=10.0)
    (out / "index.html").write_text(html, encoding="utf-8")

    # 静态检查：bg 的关键帧里不允许出现锚点位移
    bg_block = html.split("@keyframes mg_anim_0{", 1)[1].split("}", 1)[0]
    static_ok = "translate" not in bg_block

    mov = out / "bg.mov"
    r = subprocess.run([hf, "render", str(out.resolve()), "-o", str(mov.resolve()),
                        "--format", "mov", "-f", "10", "--quiet"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not mov.exists():
        print(f"Chrome 渲染失败 rc={r.returncode} {(r.stderr or '')[-200:]}")
        return 1

    png = out / "bg.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(mov),
                    "-frames:v", "1", str(png)], capture_output=True)
    from PIL import Image

    img = Image.open(png).convert("RGBA")
    alpha = img.getchannel("A")
    total = img.size[0] * img.size[1]
    opaque = sum(c for v, c in enumerate(alpha.histogram()) if v > 40)
    ratio = opaque / total
    bbox = alpha.getbbox()

    print(f"合成 {args.width}x{args.height}  Chrome 产物 {img.size}")
    print(f"alpha 覆盖: {opaque}/{total} = {ratio * 100:.1f}%   有效区 bbox={bbox}")
    print(f"bg 关键帧无锚点位移: {'是' if static_ok else '否（缺陷已复现）'}")
    ok = static_ok and ratio >= MIN_RATIO
    if not ok:
        print(f"未通过：全幅背景应覆盖整帧（≥{MIN_RATIO * 100:.0f}%），"
              f"实测 {ratio * 100:.1f}%（四分之一 ≈25% 即背景被平移了自身尺寸的一半）")
        return 1
    print("MG BG COVERAGE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
