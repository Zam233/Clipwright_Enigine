"""Animation technical QA: text/phone/motion/contact-sheet/determinism."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipwright.animation.qa import phone_report, qa_report  # noqa: E402


def _mg(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("mg_def", data)


def _frame(video: Path, t: float, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(out)], check=False)


def contact_sheets(video: Path, out_dir: Path, duration: float) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [duration * x for x in (0.1, 0.35, 0.6, 0.9)]
    frames = []
    for i, t in enumerate(times):
        p = out_dir / f"qa-{i}.png"
        _frame(video, t, p)
        if p.exists():
            frames.append((t, p))
    if not frames:
        return {"error": "no frames"}
    tw, th = 480, 270
    sheet = Image.new("RGB", (tw * 2, th * 2), "#0b1220")
    phone = Image.new("RGB", (390 * 2, 844), "#0b1220")
    for i, (t, p) in enumerate(frames):
        im = Image.open(p).convert("RGB")
        thumb = im.copy(); thumb.thumbnail((tw, th))
        sheet.paste(thumb, ((i % 2) * tw + (tw - thumb.width) // 2, (i // 2) * th + (th - thumb.height) // 2))
        ph = im.copy(); ph.thumbnail((390, 844))
        phone.paste(ph, ((i % 2) * 390 + (390 - ph.width) // 2, (i // 2) * 844 + (844 - ph.height) // 2))
    sheet.save(out_dir / "contact-sheet.png")
    phone.save(out_dir / "phone-sheet.png")
    return {"frames": len(frames), "contact": str(out_dir / "contact-sheet.png"), "phone": str(out_dir / "phone-sheet.png")}


def motion_report(video: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=2,scale=480:-1", str(Path(td) / "%04d.png")], check=False)
        files = sorted(Path(td).glob("*.png"))
        diffs = []
        prev = None
        for f in files:
            im = Image.open(f).convert("L")
            if prev is not None:
                d = ImageChops.difference(prev, im)
                hist = d.histogram()
                changed = sum(hist[16:]) / max(1, im.width * im.height)
                diffs.append(round(changed, 4))
            prev = im
        dead = sum(1 for x in diffs if x < 0.002)
        jumps = sum(1 for x in diffs if x > 0.35)
        return {"samples": len(files), "dead_ratio": round(dead / max(1, len(diffs)), 3), "jump_count": jumps, "diffs": diffs}


def determinism(mg: dict) -> dict:
    from clipwright.animation.mg_renderer import MGRenderer
    a = MGRenderer.render(mg, {}, width=int(mg.get("width") or 1920), height=int(mg.get("height") or 1080), fps=30)
    b = MGRenderer.render(mg, {}, width=int(mg.get("width") or 1920), height=int(mg.get("height") or 1080), fps=30)
    return {"same": a == b, "sha256_a": hashlib.sha256(a.encode()).hexdigest(), "sha256_b": hashlib.sha256(b.encode()).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mg-json", type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--out", type=Path, default=Path("qa"))
    args = ap.parse_args()
    report = {}
    if args.mg_json:
        mg = _mg(args.mg_json)
        report["text"] = qa_report(mg)
        report["phone"] = phone_report(mg)
        report["determinism"] = determinism(mg)
    if args.video:
        report["motion"] = motion_report(args.video)
        try:
            dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(args.video)]).decode().strip())
        except Exception:
            dur = 4.0
        report["sheets"] = contact_sheets(args.video, args.out, dur)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report.get("text", {}).get("text_issues") else 1


if __name__ == "__main__":
    raise SystemExit(main())
