"""SP1 / SP2 前置：真机渲染一个 5 秒场景两次，比对逐帧哈希（门 1 证据）。

用法::

    python scripts/spike_scene_render.py --seconds 2 --fps 30

产物：
- ``out/spike/a.mov`` / ``out/spike/b.mov``
- 标准输出一份 JSON 结论（可贴进 ``docs/spike-results-2026-10-18.md``）

退出码：0 = 渲染成功且两次逐帧一致；1 = 不可用/渲染失败/帧不一致。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 python scripts/xxx.py
    sys.path.insert(0, str(ROOT))

from clipwright.animation.hyperframes_renderer import HyperframesRenderer  # noqa: E402
from clipwright.animation.scene_renderer import build_composition_html  # noqa: E402
from tools.hash_frames import compare, hash_video  # noqa: E402

SCENE_JS = """
(function () {
  var root = document.getElementById('root');
  var box = document.createElement('div');
  box.style.cssText = 'position:absolute;width:120px;height:120px;background:#ff8a3d;';
  root.appendChild(box);
  var label = document.createElement('div');
  label.style.cssText = 'position:absolute;left:24px;top:24px;color:#f2ead8;' +
    'font:600 32px/1.2 monospace;';
  root.appendChild(label);

  window.__scene = {
    duration: %SECONDS%,
    renderFrame: function (t, ctx) {
      var p = t / %SECONDS%;
      box.style.left = Math.round(p * 1200) + 'px';
      box.style.top = Math.round(Math.sin(p * Math.PI) * 240) + 'px';
      label.textContent = 't=' + t.toFixed(3) + ' r=' + ctx.prng().toFixed(4);
    }
  };
})();
"""


def _scene_js(seconds: float) -> str:
    return SCENE_JS.replace("%SECONDS%", f"{seconds:.3f}")


async def _render(html: str, out_dir: Path, mov: Path, fps: float) -> str | None:
    return await HyperframesRenderer.render_mg_html_to_mov(
        html, str(out_dir), str(mov), fps=fps, timeout=1800.0
    )


async def main_async(args: argparse.Namespace) -> int:
    # 注意：``ais_available()`` 冷启动时**故意**返回缓存里的 default（False），
    # 由后台线程刷新——要真等探测结果必须用 ``await_available()``。
    available = await HyperframesRenderer.await_available(timeout=120.0)
    if not available:
        print(json.dumps({"ok": False, "reason": "hyperframes 不可用"}, ensure_ascii=False))
        return 1

    out_dir = ROOT / "out" / "spike"
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_composition_html(
        _scene_js(args.seconds),
        composition_id="spike",
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_sec=args.seconds,
        background="#0a0a0c",  # 全帧路径：本镜自成一体
        title="spike",
    )

    mov_a = out_dir / "a.mov"
    mov_b = out_dir / "b.mov"
    for stale in (mov_a, mov_b):
        stale.unlink(missing_ok=True)

    started = time.perf_counter()
    first = await _render(html, out_dir / "a", mov_a, args.fps)
    elapsed_a = time.perf_counter() - started
    if first is None:
        print(json.dumps({"ok": False, "reason": "第一次渲染失败"}, ensure_ascii=False))
        return 1

    started = time.perf_counter()
    second = await _render(html, out_dir / "b", mov_b, args.fps)
    elapsed_b = time.perf_counter() - started
    if second is None:
        print(json.dumps({"ok": False, "reason": "第二次渲染失败"}, ensure_ascii=False))
        return 1

    hashes_a = hash_video(mov_a, args.width, args.height)
    hashes_b = hash_video(mov_b, args.width, args.height)
    result = compare(hashes_a, hashes_b)
    payload = {
        "ok": result.equal,
        "frames": result.total_a,
        "fps": args.fps,
        "seconds": args.seconds,
        "render_sec_first": round(elapsed_a, 2),
        "render_sec_second": round(elapsed_b, 2),
        "mov_bytes": mov_a.stat().st_size if mov_a.exists() else 0,
        "first_diff": result.first_diff,
        "diffs": result.diffs[:20],
        "summary": result.summary(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.equal else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SP1：场景脚本真机渲染 + 逐帧哈希比对")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
