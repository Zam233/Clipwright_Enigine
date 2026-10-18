"""渲染"版画镜头"（PRD C3 集成验证 + 门 2 的动态证据）。

把版画库的输出**真的放进画面**：纸底 + 三墨套印 + 字符版画纹理 + 错版切换 + 字幕带 + 关键词。

流程：Python 侧渲染 PNG → base64 内联 → 场景脚本按 t 组合 → HyperFrames 渲两次 → 哈希比对 + 闪光检测。

用法::

    python scripts/render_plate_shot.py --seconds 6 --fps 30 --width 960 --height 540
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.animation.hyperframes_renderer import HyperframesRenderer  # noqa: E402
from clipwright.animation.plate import (  # noqa: E402
    compose,
    glyph_plate,
    misregistration,
    render_ground,
    split_inks,
)
from clipwright.animation.scene_renderer import build_composition_html  # noqa: E402
from tools.check_flash import analyze_video  # noqa: E402
from tools.hash_frames import compare, hash_video  # noqa: E402
from tools.lint_determinism import scan_text  # noqa: E402
from tools.plate_check import draw_silhouette  # noqa: E402

SCENE_DIR = ROOT / "examples" / "mv_poc" / "plate_shot"


def _png_data_uri(image) -> str:  # type: ignore[no-untyped-def]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def build_assets(width: int, height: int) -> dict[str, str]:
    """用版画库生成四张图：纸底 / 干净套印 / 错版套印 / 字符版画。"""
    ground = render_ground(width, height, seed=20261018)
    silhouette = draw_silhouette((width, height))
    layers = split_inks(silhouette)

    clean = ground.convert("RGBA").copy()
    clean.alpha_composite(compose(layers, size=(width, height)))

    broken = ground.convert("RGBA").copy()
    broken.alpha_composite(
        compose(misregistration(layers, offset_px=28), size=(width, height))
    )

    glyph = glyph_plate(clean, cols=max(32, width // 12), cell=12)

    return {
        "ground": _png_data_uri(ground.convert("RGB")),
        "clean": _png_data_uri(clean.convert("RGB")),
        "misreg": _png_data_uri(broken.convert("RGB")),
        "glyph": _png_data_uri(glyph.convert("RGB")),
    }


def build_cues_for_shot(
    lines: dict,
    *,
    bpm: float = 130.0,
    beat_zero_sec: float = 0.344,
) -> tuple[list[dict], float]:
    """由歌词行 + BPM 生成 cues，并取出"腐坏切入"的拍点（R-13 接入 R-14）。

    返回 ``(cues_items, cut_at)``：``cut_at`` 不再是代码里的常量，而是
    "第一个关键词 cue 吸附到半拍后的时间"。
    """
    from clipwright.schema.lyrics import LyricLine, LyricsDoc, LyricWord
    from clipwright.services.mv_cues import build_cues

    lyric_lines = []
    for raw in lines.get("lines") or []:
        words = [
            LyricWord(w=index, t0=float(raw.get("t0", 0.0)), t1=float(raw.get("t1", 0.0)))
            for index in range(1)
        ]
        lyric_lines.append(
            LyricLine(
                i=int(raw.get("i", len(lyric_lines))),
                t0=float(raw.get("t0", 0.0)),
                t1=float(raw.get("t1", 0.0)),
                text_ref=f"text({raw.get('i', 0)})",
                words=words,
            )
        )
    doc = LyricsDoc(lines=lyric_lines)
    cues = build_cues(doc, bpm=bpm, beat_zero_sec=beat_zero_sec, unit="half")
    items = [item.model_dump() for item in cues.items]
    cut_at = lines.get("cut_at")
    keyword_cue = next((item for item in cues.items if item.name.startswith("line_")), None)
    if keyword_cue is not None:
        cut_at = keyword_cue.t
    return items, float(cut_at if cut_at is not None else (lines.get("cut_at") or 2.5))


def build_scene_js(assets: dict[str, str], lines: dict, cut_at: float | None = None) -> str:
    payload = {
        "ground": assets["ground"],
        "clean": assets["clean"],
        "misreg": assets["misreg"],
        "glyph": assets["glyph"],
        "lines": lines.get("lines") or [],
        "keyword": lines.get("keyword") or "",
        "cut_at": float(cut_at if cut_at is not None else (lines.get("cut_at") or 2.5)),
        "duration_sec": lines.get("duration_sec") or 6,
    }
    source = (SCENE_DIR / "scene.js").read_text(encoding="utf-8")
    return "window.__plate = " + json.dumps(payload, ensure_ascii=False) + ";\n" + source


async def _render(html: str, out: Path, mov: Path, fps: float) -> str | None:
    return await HyperframesRenderer.render_mg_html_to_mov(
        html, str(out), str(mov), fps=fps, timeout=1800.0
    )


async def main_async(args: argparse.Namespace) -> int:
    if not await HyperframesRenderer.await_available(timeout=120.0):
        print(json.dumps({"ok": False, "reason": "hyperframes 不可用"}, ensure_ascii=False))
        return 1

    lines = json.loads((SCENE_DIR / "lines.json").read_text(encoding="utf-8"))
    cue_items, cut_at = build_cues_for_shot(
        lines, bpm=args.bpm, beat_zero_sec=args.beat_zero
    )
    scene_js = build_scene_js(build_assets(args.width, args.height), lines, cut_at)
    hits = scan_text(scene_js, str(SCENE_DIR / "scene.js"))
    if hits:
        print(json.dumps({"ok": False, "reason": "scene.js 未过确定性 lint",
                          "hits": [h.render() for h in hits]}, ensure_ascii=False, indent=2))
        return 1

    out_dir = ROOT / "out" / "plate_shot"
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_composition_html(
        scene_js,
        composition_id="plate_shot",
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_sec=args.seconds,
        background="#0a0a0c",
        title="plate_shot",
    )
    (out_dir / "plate_shot.html").write_text(html, encoding="utf-8")

    mov_a, mov_b = out_dir / "a.mov", out_dir / "b.mov"
    for stale in (mov_a, mov_b):
        stale.unlink(missing_ok=True)

    started = time.perf_counter()
    if await _render(html, out_dir / "a", mov_a, args.fps) is None:
        print(json.dumps({"ok": False, "reason": "第一次渲染失败"}, ensure_ascii=False))
        return 1
    elapsed_a = time.perf_counter() - started
    if await _render(html, out_dir / "b", mov_b, args.fps) is None:
        print(json.dumps({"ok": False, "reason": "第二次渲染失败"}, ensure_ascii=False))
        return 1

    comparison = compare(
        hash_video(mov_a, args.width, args.height),
        hash_video(mov_b, args.width, args.height),
    )
    flash = analyze_video(mov_a, fps=args.fps)
    report = {
        "ok": comparison.equal and flash.ok,
        "scene": "plate_shot",
        "frames": comparison.total_a,
        "fps": args.fps,
        "seconds": args.seconds,
        "deterministic": comparison.equal,
        "first_diff": comparison.first_diff,
        "render_sec": round(elapsed_a, 2),
        "mov_bytes": mov_a.stat().st_size if mov_a.exists() else 0,
        "flash_ok": flash.ok,
        "flash_violations": flash.violations,
        "html_bytes": (out_dir / "plate_shot.html").stat().st_size,
        "bpm": args.bpm,
        "beat_zero_sec": args.beat_zero,
        "cut_at": cut_at,
        "cues": cue_items,
    }
    # 工程包的"时间唯一真相"：用标准形态落盘（grid + items），供 mv_bundle.load_cues 读取
    from clipwright.services.mv_cues import BeatGrid, Cue, CuesDoc, save_cues

    cues_doc = CuesDoc(
        grid=BeatGrid(bpm=args.bpm, beat_zero_sec=args.beat_zero),
        items=[Cue(**item) for item in cue_items],
    )
    save_cues(out_dir / "cues.json", cues_doc)
    save_cues(SCENE_DIR / "cues.json", cues_doc)
    (SCENE_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="版画镜头：渲染 + 确定性 + 闪光三闸")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--bpm", type=float, default=130.0)
    parser.add_argument("--beat-zero", type=float, default=0.344, dest="beat_zero")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
