"""Reference video inspection: contact sheet + scene-cut timestamps."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def scene_cuts(video: Path) -> list[float]:
    try:
        out = subprocess.check_output([
            "ffmpeg", "-hide_banner", "-i", str(video),
            "-filter:v", "select='gt(scene,0.30)',showinfo",
            "-f", "null", "-",
        ], stderr=subprocess.STDOUT).decode("utf-8", "ignore")
    except Exception:
        return []
    cuts: list[float] = []
    for line in out.splitlines():
        if "pts_time:" not in line:
            continue
        try:
            cuts.append(round(float(line.split("pts_time:")[1].split()[0]), 3))
        except Exception:
            pass
    return cuts


def contact_sheet(video: Path, out: Path, duration: float, n: int = 8) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cols, rows, tw, th = 4, 2, 480, 270
    sheet = Image.new("RGB", (cols * tw, rows * th), "#111")
    d = ImageDraw.Draw(sheet)
    for i in range(n):
        t = duration * (i + 0.5) / n
        frame = out.parent / f"_ref_{i}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(frame)], check=False)
        if frame.exists():
            im = Image.open(frame).convert("RGB"); im.thumbnail((tw, th))
            sheet.paste(im, ((i % cols) * tw + (tw - im.width) // 2, (i // cols) * th + (th - im.height) // 2))
            d.text(((i % cols) * tw + 6, (i // cols) * th + 6), f"{t:.1f}s", fill="white")
            frame.unlink(missing_ok=True)
    sheet.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, default=Path("reference-inspect"))
    args = ap.parse_args()
    try:
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(args.video)]).decode().strip())
    except Exception:
        dur = 8.0
    sheet = contact_sheet(args.video, args.out / "contact-sheet.png", dur)
    cuts = scene_cuts(args.video)
    report = {"video": str(args.video), "duration": dur, "cuts": cuts, "bpm_hint": round(60 / (sum(cuts[i+1]-cuts[i] for i in range(len(cuts)-1)) / max(1, len(cuts)-1)), 1) if len(cuts) > 1 else None, "contact_sheet": str(sheet)}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
