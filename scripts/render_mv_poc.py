"""R-04：渲染聊天界面场景（门 1 收口证据）。

流程：
1. 读 ``examples/mv_poc/chat/{scene.js, chat_script.json}``；
2. 注入 ``window.__chat`` 数据后生成全帧合成 HTML（不透明底 = 全帧路径）；
3. 渲染两次 → 逐帧 sha256 比对 → 跑闪光检测 → 输出 JSON 报告。

用法::

    python scripts/render_mv_poc.py --seconds 12 --fps 30 --width 960 --height 540
    python scripts/render_mv_poc.py --seconds 30 --fps 60 --width 1920 --height 1080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.animation.hyperframes_renderer import HyperframesRenderer  # noqa: E402
from clipwright.animation.scene_renderer import build_composition_html  # noqa: E402
from tools.check_flash import analyze_video  # noqa: E402
from tools.hash_frames import compare, hash_video  # noqa: E402
from tools.lint_determinism import scan_text  # noqa: E402

CHAT_DIR = ROOT / "examples" / "mv_poc" / "chat"


def _scene_js(script: dict) -> str:
    source = (CHAT_DIR / "scene.js").read_text(encoding="utf-8")
    payload = json.dumps(script, ensure_ascii=False, sort_keys=True)
    return f"window.__chat = {payload};\n{source}"


async def _render(html: str, out: Path, mov: Path, fps: float) -> str | None:
    return await HyperframesRenderer.render_mg_html_to_mov(
        html, str(out), str(mov), fps=fps, timeout=1800.0
    )


async def main_async(args: argparse.Namespace) -> int:
    if not await HyperframesRenderer.await_available(timeout=120.0):
        print(json.dumps({"ok": False, "reason": "hyperframes 不可用"}, ensure_ascii=False))
        return 1

    script = json.loads((CHAT_DIR / "chat_script.json").read_text(encoding="utf-8"))
    scene_js = _scene_js(script)

    lint_hits = scan_text(scene_js, str(CHAT_DIR / "scene.js"))
    if lint_hits:
        print(json.dumps(
            {"ok": False, "reason": "scene.js 未过确定性 lint", "hits": [h.render() for h in lint_hits]},
            ensure_ascii=False, indent=2))
        return 1

    out_dir = ROOT / "out" / "mv_poc"
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_composition_html(
        scene_js,
        composition_id="chat_poc",
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_sec=args.seconds,
        background="#0a0a0c",  # 该镜自成一体：全帧合成（决策由本脚本代表动画 agent 记录）
        title="chat_poc",
    )
    (out_dir / "chat.html").write_text(html, encoding="utf-8")

    mov_a, mov_b = out_dir / "chat_a.mov", out_dir / "chat_b.mov"
    for stale in (mov_a, mov_b):
        stale.unlink(missing_ok=True)

    started = time.perf_counter()
    if await _render(html, out_dir / "a", mov_a, args.fps) is None:
        print(json.dumps({"ok": False, "reason": "第一次渲染失败"}, ensure_ascii=False))
        return 1
    elapsed_a = time.perf_counter() - started

    started = time.perf_counter()
    if await _render(html, out_dir / "b", mov_b, args.fps) is None:
        print(json.dumps({"ok": False, "reason": "第二次渲染失败"}, ensure_ascii=False))
        return 1
    elapsed_b = time.perf_counter() - started

    hashes_a = hash_video(mov_a, args.width, args.height)
    hashes_b = hash_video(mov_b, args.width, args.height)
    comparison = compare(hashes_a, hashes_b)

    flash = analyze_video(mov_a, fps=args.fps)
    report = {
        "ok": comparison.equal and flash.ok,
        "frames": comparison.total_a,
        "fps": args.fps,
        "seconds": args.seconds,
        "deterministic": comparison.equal,
        "first_diff": comparison.first_diff,
        "render_sec_first": round(elapsed_a, 2),
        "render_sec_second": round(elapsed_b, 2),
        "mov_bytes": mov_a.stat().st_size if mov_a.exists() else 0,
        "flash_ok": flash.ok,
        "flash_violations": flash.violations,
        "composition_mode": "opaque/full_frame",
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R-04 聊天界面场景渲染 + 三闸验收")
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
