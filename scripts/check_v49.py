"""V49 复核：七类形态各自的片段是否都 ≥10 秒且过三闸。

只读已有产物，不重新渲染（渲染慢，按需单独跑）：

- 四类纯图形：`out/archetypes/<kind>/a.mov` + 同目录 `report.json`
- 界面叙事：`out/mv_poc/chat_a.mov`
- 版画拼贴：`out/plate_shot/a.mov`
- 纯图形歌词：`out/archetypes/lyric_graphic/a.mov`（第 7 类，本文件伴随的样例）

三闸 = 时长 ≥10s + 两次渲染逐帧一致（report.deterministic）+ 闪光 0 违规（report.flash_ok）。

用法::

    python scripts/check_v49.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: (展示名, 视频, 闸报告所在目录)
SEGMENTS: list[tuple[str, str, str]] = [
    ("排版动画", "out/archetypes/typography/a.mov", "out/archetypes/typography"),
    ("生成艺术", "out/archetypes/generative/a.mov", "out/archetypes/generative"),
    ("代码与数据", "out/archetypes/code_data/a.mov", "out/archetypes/code_data"),
    ("VJ 光条", "out/archetypes/vj_light/a.mov", "out/archetypes/vj_light"),
    ("纯图形歌词", "out/archetypes/lyric_graphic/a.mov", "out/archetypes/lyric_graphic"),
    ("界面叙事", "out/mv_poc/chat_a.mov", "out/mv_poc"),
    ("版画拼贴", "out/plate_shot/a.mov", "out/plate_shot"),
]
MIN_SEC = 10.0


def _probe(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    streams = json.loads(out).get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return round(float(video.get("duration") or 0.0), 2)


def _gate_report(directory: Path) -> dict:
    path = directory / "report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 坏报告按缺失处理
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V49：七类形态片段复核")
    parser.parse_args(argv)

    rows: list[dict] = []
    for name, rel, report_dir in SEGMENTS:
        video = ROOT / rel
        if not video.exists():
            rows.append({"kind": name, "path": rel, "missing": True})
            continue
        duration = _probe(video)
        report = _gate_report(ROOT / report_dir)
        rows.append({
            "kind": name,
            "path": rel,
            "duration_sec": duration,
            "ge_min": duration >= MIN_SEC - 0.1,
            "frames": report.get("frames") or report.get("frames_compared"),
            "deterministic": report.get("deterministic"),
            "flash_ok": report.get("flash_ok"),
        })

    ok = all(
        row.get("ge_min") and row.get("deterministic") is True and row.get("flash_ok") is True
        for row in rows
    )
    report = {"ok": ok, "min_sec": MIN_SEC, "count": len(rows), "segments": rows}
    target = ROOT / "out" / "archetypes" / "coverage-v49.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
