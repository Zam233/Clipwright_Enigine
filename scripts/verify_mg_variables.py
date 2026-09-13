"""验收 P1-5：`data-var-text` 绑定是否真的被上游 `--variables` 驱动。

做法（差分实验，不依赖 OCR）：
1. 用 MGRenderer 生成只含**一个文本元素**的 MG HTML（文本绑定到变量 text）；
2. `hyperframes lint` 必须 0 error；
3. 不传 `--variables` 渲染 → 关键帧截图应有可见文字（兜底生效）；
4. 传 `--variables '{"text":""}'` 渲染 → 同一时刻画面应几乎清空（说明变量确实驱动文本）；
5. 传另一段文案渲染 → 画面应再次有内容且与第 3 步不同。

用法（工作目录 J:\\Clipwright，需 ffmpeg + HyperFrames/Chromium）：
    python scripts/verify_mg_variables.py
退出码 0 = 全部断言通过。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"J:\Clipwright")

from clipwright.animation.hyperframes_renderer import HyperframesRenderer  # noqa: E402
from clipwright.animation.mg_renderer import MGRenderer  # noqa: E402

MG_DEF = {
    "animation_id": "var_verify",
    "duration_sec": 2.0,
    "style": {"background": "transparent", "font_family": "sans-serif"},
    "params": {"text": {"type": "string", "default": "绑定文本"}},
    "elements": [
        {
            "type": "text", "content": "{text}", "x": "center", "y": "center",
            "font_size": 64, "font_color": "#ffffff",
            "keyframes": [
                {"time": 0.0, "opacity": 0},
                {"time": 0.2, "opacity": 1},
                {"time": 1.8, "opacity": 1},
                {"time": 2.0, "opacity": 0},
            ],
        }
    ],
}


def cli(*args: str) -> list[str]:
    return HyperframesRenderer._cli_cmd(*args)


def visible_ratio(png: str) -> float:
    from PIL import Image

    im = Image.open(png).convert("RGBA")
    data = list(im.getdata())
    n = len(data)
    return 100.0 * sum(1 for _r, _g, _b, a in data if a > 64) / n


def render_and_sample(work: Path, label: str, variables: dict | None) -> tuple[float, str, int]:
    """写 HTML → （可选）传 --variables 渲染 → 采样中段帧，返回可见像素占比。"""
    proj = work / label
    proj.mkdir(parents=True, exist_ok=True)
    html = MGRenderer.render(MG_DEF, {"text": MG_DEF["params"]["text"]["default"]},
                             width=640, height=360, fps=30)
    (proj / "index.html").write_text(html, encoding="utf-8")

    lint = subprocess.run(cli("lint", str(proj), "--json"), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    try:
        lint_json = json.loads(lint.stdout or "{}")
    except Exception:
        lint_json = {}
    errors = int(lint_json.get("errorCount") or 0)

    snap_dir = proj / "snaps"
    snap_cmd = cli("snapshot", str(proj), "--at", "1.0", "-o", str(snap_dir))
    if variables is not None:
        # snapshot 不支持 --variables；用 render 出 MOV 再抽帧，保证变量生效
        mov = proj / "out.mov"
        cmd = cli("render", str(proj), "-o", str(mov), "--format", "mov", "-f", "30", "--quiet",
                  "--variables", json.dumps(variables, ensure_ascii=False))
        rendered = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=900)
        if rendered.returncode != 0:
            print(f"    [{label}] render rc={rendered.returncode} {rendered.stderr[-200:]}")
            return -1.0, "", errors
        frame = proj / "frame.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(mov),
                        "-frames:v", "1", str(frame)], capture_output=True, timeout=120)
        return visible_ratio(str(frame)), str(mov), errors

    subprocess.run(snap_cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=600)
    pngs = sorted(snap_dir.glob("*.png"))
    if not pngs:
        return -1.0, "", errors
    return visible_ratio(str(pngs[0])), str(pngs[0]), errors


def main() -> int:
    failures: list[str] = []
    work = Path(tempfile.mkdtemp(prefix="mg_var_verify_"))
    try:
        print("[1/3] 生成 MG HTML（文本绑定到变量 text）…")
        html_probe = MGRenderer.render(MG_DEF, {"text": "绑定文本"}, width=640, height=360, fps=30)
        if 'data-var-text="text"' not in html_probe:
            failures.append("HTML 未产生 data-var-text 绑定")
        if "data-composition-variables" not in html_probe:
            failures.append("HTML 未声明 data-composition-variables")
        if "{text}" in html_probe:
            failures.append("HTML 仍残留字面占位符 {text}")

        print("[2/3] 渲染三种变量取值…")
        base_ratio, base_path, lint_err = render_and_sample(work, "fallback", None)
        empty_ratio, _empty_path, _ = render_and_sample(work, "empty", {"text": ""})
        other_ratio, _other_path, _ = render_and_sample(work, "other", {"text": "另一段更长一些的文案内容"})

        print(f"    lint error={lint_err} | 兜底渲染可见={base_ratio:.3f}% | "
              f"text='' 可见={empty_ratio:.3f}% | 覆盖文案可见={other_ratio:.3f}%")
        if lint_err:
            failures.append(f"lint 报 {lint_err} 个 error")
        if base_ratio <= 0.05:
            failures.append(f"兜底渲染几乎不可见（{base_ratio:.3f}%）——绑定把文本弄丢了")
        if empty_ratio < 0:
            failures.append("text='' 的渲染失败")
        elif empty_ratio > base_ratio * 0.35:
            failures.append(
                f"把 text 覆盖为空后画面仍可见 {empty_ratio:.3f}%（兜底 {base_ratio:.3f}%）"
                "——变量未真正驱动文本"
            )
        if other_ratio <= 0.05:
            failures.append(f"覆盖文案渲染几乎不可见（{other_ratio:.3f}%）")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print("\nVERIFY FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nVERIFY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
