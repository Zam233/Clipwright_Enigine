"""生成 SP1 用的合成 HTML（诊断用，非产品代码）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.animation.scene_renderer import build_composition_html  # noqa: E402

SCENE_JS = """
(function () {
  var root = document.getElementById('root');
  var box = document.createElement('div');
  box.style.cssText = 'position:absolute;width:60px;height:60px;background:#ff8a3d;';
  root.appendChild(box);
  var label = document.createElement('div');
  label.style.cssText = 'position:absolute;left:12px;top:12px;color:#f2ead8;font:600 16px monospace;';
  root.appendChild(label);
  window.__scene = {
    duration: 1.0,
    renderFrame: function (t, ctx) {
      box.style.left = Math.round(t * 200) + 'px';
      label.textContent = 't=' + t.toFixed(3) + ' r=' + ctx.prng().toFixed(3);
    }
  };
})();
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out/spike/manual")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--fps", type=float, default=24)
    parser.add_argument("--duration", type=float, default=1.0)
    args = parser.parse_args()

    directory = ROOT / args.out
    directory.mkdir(parents=True, exist_ok=True)
    html = build_composition_html(
        SCENE_JS,
        composition_id="spike",
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_sec=args.duration,
        background="#0a0a0c",
    )
    target = directory / "index.html"
    target.write_text(html, encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
