"""形态样例渲染（V49 部分兑现）：四类纯代码形态各渲 10 秒并过三闸。

形态（PRD §III.2 的 1/3/5/6 类）：``typography``、``generative``、``code_data``、``vj_light``。

用法::

    python scripts/render_archetypes.py                       # 四类全跑
    python scripts/render_archetypes.py --kinds typography    # 只跑一类

产物：``out/archetypes/<kind>/{a.mov,report.json}`` 与总报告 ``out/archetypes/report.json``。
退出码：0 = 全部通过（确定性 + 闪光）；1 = 有失败。
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

SCENE_DIR = ROOT / "examples" / "mv_poc" / "archetypes"
#: 形态 → 标题（标题会显示在画面里，便于人工核对）
ARCHETYPES: dict[str, str] = {
    "typography": "kinetic typography",
    "generative": "generative lines",
    "code_data": "code & counters",
    "vj_light": "vj light bars",
    # V49 第 7 类：纯图形歌词（无外部素材，靠排版 + 逐词高亮）
    "lyric_graphic": "pure lyric graphic",
}
#: 需要额外数据的形态 → 数据文件（歌词文本刻意不进 scene.js，V31）
DATA_FILES: dict[str, str] = {"lyric_graphic": "lyric_lines.json"}


def _scene_js(kind: str, seconds: float) -> str:
    source = (SCENE_DIR / "scene.js").read_text(encoding="utf-8")
    payload: dict[str, object] = {"kind": kind, "duration_sec": seconds, "title": ARCHETYPES[kind]}
    data_name = DATA_FILES.get(kind)
    if data_name:
        data = json.loads((SCENE_DIR / data_name).read_text(encoding="utf-8"))
        payload["lines"] = data.get("lines") or []
    return "window.__arch = " + json.dumps(payload, ensure_ascii=False) + ";\n" + source


async def _render(html: str, out: Path, mov: Path, fps: float) -> str | None:
    return await HyperframesRenderer.render_mg_html_to_mov(
        html, str(out), str(mov), fps=fps, timeout=1800.0
    )


async def render_one(kind: str, args: argparse.Namespace) -> dict:
    js = _scene_js(kind, args.seconds)
    hits = scan_text(js, f"{kind}/scene.js")
    if hits:
        return {"kind": kind, "ok": False, "reason": "lint", "hits": [h.render() for h in hits]}

    out_dir = ROOT / "out" / "archetypes" / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_composition_html(
        js,
        composition_id=kind,
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_sec=args.seconds,
        background="#08080b",
        title=kind,
    )
    mov_a, mov_b = out_dir / "a.mov", out_dir / "b.mov"
    for stale in (mov_a, mov_b):
        stale.unlink(missing_ok=True)

    started = time.perf_counter()
    if await _render(html, out_dir / "a", mov_a, args.fps) is None:
        return {"kind": kind, "ok": False, "reason": "render_failed_first"}
    elapsed = time.perf_counter() - started
    if await _render(html, out_dir / "b", mov_b, args.fps) is None:
        return {"kind": kind, "ok": False, "reason": "render_failed_second"}

    comparison = compare(
        hash_video(mov_a, args.width, args.height),
        hash_video(mov_b, args.width, args.height),
    )
    flash = analyze_video(mov_a, fps=args.fps)
    report = {
        "kind": kind,
        "title": ARCHETYPES[kind],
        "ok": comparison.equal and flash.ok,
        "frames": comparison.total_a,
        "deterministic": comparison.equal,
        "first_diff": comparison.first_diff,
        "flash_ok": flash.ok,
        "flash_violations": flash.violations,
        "render_sec": round(elapsed, 2),
        "mov_bytes": mov_a.stat().st_size if mov_a.exists() else 0,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


async def main_async(args: argparse.Namespace) -> int:
    if not await HyperframesRenderer.await_available(timeout=120.0):
        print(json.dumps({"ok": False, "reason": "hyperframes 不可用"}, ensure_ascii=False))
        return 1

    kinds = args.kinds or list(ARCHETYPES)
    unknown = [k for k in kinds if k not in ARCHETYPES]
    if unknown:
        print(f"未知形态: {unknown}", file=sys.stderr)
        return 1

    results = []
    for kind in kinds:
        print(f"… 渲染 {kind}", file=sys.stderr)
        results.append(await render_one(kind, args))

    summary = {
        "ok": all(r.get("ok") for r in results),
        "count": len(results),
        "passed": [r["kind"] for r in results if r.get("ok")],
        "failed": [r["kind"] for r in results if not r.get("ok")],
        "seconds": args.seconds,
        "fps": args.fps,
        "results": results,
    }
    target = ROOT / "out" / "archetypes" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="形态样例渲染（V49）")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--fps", type=float, default=24.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--kinds", nargs="*", default=[], help="只渲指定形态")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
