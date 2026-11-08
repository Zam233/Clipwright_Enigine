"""V35：拿真管线产出的时间线去问"这算不算 lyric_mv"。

做三件事：

1. 用 `category_plugin_id=lyric_mv` 跑一次真管线（dry-run，不出片）；
2. 从 trace 的 `timeline_snapshot` 取出真实时间线；
3. 调 `POST /api/type-maker/lyric_mv/genre-check`，看指标与判定。

**为什么不直接写死一条"像 MV"的时间线**：那样只能证明判定器会算数，证明不了
"这条类型真的在生产链路里跑得起来、产出被判为它自己"。

用法::

    python scripts/check_genre_v35.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TERMINAL = {"completed", "failed", "cancelled", "canceled"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V35：真时间线的类型判定")
    parser.add_argument("--type", default="lyric_mv")
    parser.add_argument("--topic", default="三墨之间")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--audio-sec", type=float, default=0.0,
                        help="配音/音乐时长（MV 必须给，否则场景会按 LLM 长镜切）")
    parser.add_argument("--bpm", type=float, default=0.0, help="BPM（给了才建拍点表）")
    parser.add_argument("--script", default="", help="歌词文本（一行一句）")
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    started = time.perf_counter()
    report: dict = {"ok": False, "type_id": args.type}
    extra: dict = {
        "creative_prompt": "版画质感，三墨套印，一句词一镜，切点落拍。",
        "subtitle_enabled": True,
    }
    if args.audio_sec > 0:
        extra["audio_duration_sec"] = args.audio_sec
    if args.bpm > 0:
        extra["bpm"] = args.bpm
        extra["cut_on_beat"] = True
    if args.script:
        extra["script_text"] = args.script
    report["extra_params"] = extra
    with TestClient(app) as client:
        res = client.post("/api/pipeline/run-async", json={
            "persona_id": "default",
            "category_plugin_id": args.type,
            "topic": args.topic,
            "dry_run": True,
            "extra_params": extra,
        })
        report["start_http"] = res.status_code
        if res.status_code >= 400:
            report["error"] = res.text[:400]
            _write(report, started)
            return 1
        pipeline_id = str((res.json() or {}).get("pipeline_id") or "")
        report["pipeline_id"] = pipeline_id

        status = ""
        deadline = time.perf_counter() + args.timeout
        while time.perf_counter() < deadline:
            status = str(client.get(f"/api/pipeline/status/{pipeline_id}").json().get("status") or "")
            if status.lower() in TERMINAL:
                break
            time.sleep(args.interval)
        report["status"] = status

        events = client.get(f"/api/pipeline/trace/{pipeline_id}").json()
        if not isinstance(events, list):
            events = []
        snapshot = next(
            (e.get("detail") for e in events if e.get("type") == "timeline_snapshot"), None
        )
        if not snapshot:
            report["error"] = "trace 里没有 timeline_snapshot，无法判定"
            _write(report, started)
            return 1
        report["timeline"] = {
            "duration_sec": snapshot.get("duration_sec"),
            "tracks": [
                {"name": t.get("name"), "kind": t.get("kind"), "clips": len(t.get("clips") or [])}
                for t in snapshot.get("tracks") or []
            ],
        }
        dump = ROOT / "out" / "v35" / f"timeline-{pipeline_id}.json"
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        report["timeline_dump"] = str(dump.relative_to(ROOT))

        scenes = [
            {"title": s.get("title"), "description": s.get("description")}
            for s in (next(
                (e.get("detail") or {}).get("scenes")
                for e in events
                if e.get("type") == "structure" and isinstance(e.get("detail"), dict)
            ) or [])
        ] if any(e.get("type") == "structure" for e in events) else []

        check = client.post(
            f"/api/type-maker/{args.type}/genre-check",
            json={
                "timeline": snapshot,
                "scenes": scenes,
                "bpm": args.bpm or 0,
            },
        )
        report["genre_check_http"] = check.status_code
        if check.status_code >= 400:
            report["error"] = check.text[:400]
            _write(report, started)
            return 1
        result = check.json()
        report["genre_check"] = {
            "ok": result.get("ok"),
            "judge": result.get("judge"),
            "traits_declared": result.get("traits_declared"),
            "summary": result.get("summary"),
            "measured": {
                key: round(float(value), 3)
                for key, value in (result.get("measured") or {}).items()
                if isinstance(value, (int, float))
            },
            "traits": result.get("traits"),
            "mismatches": result.get("mismatches"),
        }
        report["ok"] = bool(result.get("ok"))
    _write(report, started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write(report: dict, started: float) -> None:
    report["elapsed_sec"] = round(time.perf_counter() - started, 1)
    target = ROOT / "out" / "v35" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
