"""V34：4K hero 单帧的锯齿检查（渲一帧、量边缘）。

**为什么只渲一帧**：验收问的是"4K hero 特写有没有锯齿"，这属于栅格化质量，不需要
整段视频——渲 0.4 秒取中间一帧即可。

**判据（"和超采样参考比"标准做法）**：抗锯齿的本质是"边缘有没有混叠"。
所以同一场景渲两版：

- 实测：3840x2160 原生；
- 参考：7680x4320（2 倍超采样）再用 BOX 缩到 4K——这是高质量抗锯齿的近似真值。

若两者在边缘带上的平均差异很小，说明原生 4K 的边缘已经"平滑到和超采样几乎一样"；
再拿一个**最近邻放大**的劣质对照，如果它的差异明显更大，就证明这个指标真能分辨锯齿。
（第一版我拿"双线性缩小再 NN 放大"当对照，结果它反而更平滑——那是个坏对照，已换掉。）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np  # noqa: F401 - SCENE_JS 之外的数值处理留在本文件

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.aliasing import edge_band_mae, edge_metrics, load_gray  # noqa: E402

WIDTH, HEIGHT = 3840, 2160
SUPERSAMPLE = 2
SECONDS = 0.4
FPS = 30

SCENE_JS = r"""
(function () {
  var root = document.getElementById('root');
  var W = root.clientWidth || 3840;
  var H = root.clientHeight || 2160;
  var stage = document.createElement('div');
  stage.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;' +
    'background:#0e0d0c;overflow:hidden';
  root.appendChild(stage);
  // hero：大号字形 + 一条细斜线（斜线最容易暴露台阶）
  var svgNS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('width', String(W));
  svg.setAttribute('height', String(H));
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  stage.appendChild(svg);

  var defs = document.createElementNS(svgNS, 'defs');
  var grad = document.createElementNS(svgNS, 'linearGradient');
  grad.setAttribute('id', 'ink');
  grad.setAttribute('x1', '0'); grad.setAttribute('y1', '0');
  grad.setAttribute('x2', '1'); grad.setAttribute('y2', '1');
  [['0', '#c8342a'], ['0.55', '#f2ead8'], ['1', '#2b4f8a']].forEach(function (pair) {
    var stop = document.createElementNS(svgNS, 'stop');
    stop.setAttribute('offset', pair[0]);
    stop.setAttribute('stop-color', pair[1]);
    grad.appendChild(stop);
  });
  defs.appendChild(grad);
  svg.appendChild(defs);

  var text = document.createElementNS(svgNS, 'text');
  text.setAttribute('x', String(W * 0.5));
  text.setAttribute('y', String(H * 0.62));
  text.setAttribute('text-anchor', 'middle');
  text.setAttribute('font-family', 'Georgia, "Songti SC", serif');
  text.setAttribute('font-weight', '700');
  text.setAttribute('font-size', String(Math.round(H * 0.62)));
  text.setAttribute('fill', 'url(#ink)');
  text.textContent = '墨';
  svg.appendChild(text);

  var line = document.createElementNS(svgNS, 'line');
  line.setAttribute('x1', String(W * 0.06));
  line.setAttribute('y1', String(H * 0.88));
  line.setAttribute('x2', String(W * 0.94));
  line.setAttribute('y2', String(H * 0.12));
  line.setAttribute('stroke', '#f2ead8');
  line.setAttribute('stroke-width', '3');
  svg.appendChild(line);

  window.__scene = {
    duration: 0.4,
    renderFrame: function () { /* 静态 hero：不随时间变化 */ }
  };
})();
"""


def _extract_frame(mov: Path, at_sec: float, out_png: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{at_sec}", "-i", str(mov),
         "-frames:v", "1", str(out_png)],
        check=True, capture_output=True,
    )


def _nn_control(src_png: Path, out_png: Path, factor: int = 4) -> None:
    """劣质对照：最近邻缩小再用最近邻放大（没有任何抗锯齿滤波）。"""
    from PIL import Image

    image = Image.open(src_png).convert("RGB")
    small = image.resize(
        (max(1, image.width // factor), max(1, image.height // factor)), Image.NEAREST
    )
    small.resize(image.size, Image.NEAREST).save(out_png)


async def _render_async(html: str, out_dir: Path, stem: str) -> Path | None:
    from clipwright.animation.hyperframes_renderer import HyperframesRenderer

    mov = out_dir / f"{stem}.mov"
    mov.unlink(missing_ok=True)
    ok = await HyperframesRenderer.render_mg_html_to_mov(
        html, str(out_dir / stem), str(mov), fps=FPS, timeout=1800.0
    )
    return mov if ok is not None and mov.exists() else None


async def _run(args: argparse.Namespace) -> int:
    from clipwright.animation.hyperframes_renderer import HyperframesRenderer
    from clipwright.animation.scene_renderer import build_composition_html

    started = time.perf_counter()
    out_dir = ROOT / "out" / "v34"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"ok": False, "width": WIDTH, "height": HEIGHT}

    if not await HyperframesRenderer.await_available(timeout=120.0):
        report["error"] = "HyperFrames 不可用，无法渲染 4K 单帧"
        _write(report, started)
        return 1

    def _html(width: int, height: int, comp_id: str) -> str:
        return build_composition_html(
            SCENE_JS,
            composition_id=comp_id,
            width=width,
            height=height,
            fps=FPS,
            duration_sec=SECONDS,
            background="#0e0d0c",
            title=comp_id,
        )

    # ① 实测：4K 原生
    render_started = time.perf_counter()
    mov_4k = await _render_async(_html(WIDTH, HEIGHT, "hero4k"), out_dir, "hero4k")
    if mov_4k is None:
        report["error"] = "4K 渲染失败"
        _write(report, started)
        return 1
    report["render_sec_4k"] = round(time.perf_counter() - render_started, 2)

    # ② 参考：2 倍超采样后 BOX 缩回 4K（高质量抗锯齿的近似真值）
    ss_w, ss_h = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE
    ss_started = time.perf_counter()
    mov_ss = await _render_async(_html(ss_w, ss_h, "hero8k"), out_dir, "hero8k")
    if mov_ss is None:
        report["error"] = "超采样参考渲染失败"
        _write(report, started)
        return 1
    report["render_sec_supersample"] = round(time.perf_counter() - ss_started, 2)

    frame = out_dir / "hero4k_frame.png"
    _extract_frame(mov_4k, SECONDS / 2, frame)
    ref_raw = out_dir / "hero8k_frame.png"
    _extract_frame(mov_ss, SECONDS / 2, ref_raw)

    from PIL import Image

    reference = out_dir / "hero4k_reference.png"
    with Image.open(ref_raw) as big:
        big.convert("RGB").resize((WIDTH, HEIGHT), Image.BOX).save(reference)
    nn_control = out_dir / "hero4k_frame_nn.png"
    _nn_control(frame, nn_control)

    with Image.open(frame) as image:
        report["frame_size"] = list(image.size)
    report["native_4k"] = edge_metrics(load_gray(frame))
    report["nearest_upscale_control"] = edge_metrics(load_gray(nn_control))
    report["vs_supersample_native"] = edge_band_mae(frame, reference, frame)
    report["vs_supersample_nn"] = edge_band_mae(nn_control, reference, frame)

    native_err = report["vs_supersample_native"]["mean_abs_diff_edge_band"]
    nn_err = report["vs_supersample_nn"]["mean_abs_diff_edge_band"]
    native_all = report["vs_supersample_native"]["mean_abs_diff_all"]
    report["gain_vs_nn"] = round(nn_err - native_err, 3)
    report["native_over_nn"] = round(native_err / nn_err, 3) if nn_err else None
    # 判据（相对基准，不用拍脑袋的绝对值）：
    #   1. 原生与超采样参考的边缘差，必须远小于"无抗锯齿放大"的同类差异（<40%）；
    #   2. 全图平均差必须在可见阈值内（≤1 灰阶，满量程 255）。
    report["ok"] = bool(
        report["frame_size"] == [WIDTH, HEIGHT]
        and nn_err > 0
        and native_err < nn_err * 0.40
        and native_all <= 1.0
    )
    _write(report, started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write(report: dict, started: float) -> None:
    report.setdefault("elapsed_sec", round(time.perf_counter() - started, 1))
    target = ROOT / "out" / "v34" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V34：4K hero 单帧锯齿检查")
    parser.parse_args(argv)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
