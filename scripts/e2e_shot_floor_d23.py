"""D23 真机对照：同一个类型下限，`record` 与 `extend` 两种策略产出什么样。

做法：同一主题、同一份规划（24 秒）、同一个 `min_shot_ms=9000`，跑两次 dry-run：

- `record`：片段照旧，但 trace 里出现 `shot_floor` 事件 + 逐条 C2；
- `extend`：低于下限的场景被抬到 9 秒，成片总时长随之变长。

**这是给 D23 决策用的证据，不替谁拍板**：默认策略仍是 `off`。

用法::

    python scripts/e2e_shot_floor_d23.py
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
FLOOR_MS = 9000


def _plan() -> dict:
    return {
        "type": "vlog_daily",
        "target_duration_sec": 24.0,
        "sections": [
            {"id": "A", "name": "开场", "t0": 0.0, "t1": 8.0, "purpose": "建立场景",
             "screen_kinds": ["plate"]},
            {"id": "B", "name": "主体", "t0": 8.0, "t1": 24.0, "purpose": "展开",
             "screen_kinds": ["plate"]},
        ],
        "screen_kinds": ["plate"],
        "material_strategy": {"gaps": []},
        "legacy": False,
    }


def _run(client, *, policy: str, topic: str, timeout: float, interval: float) -> dict:
    extra = {
        "plan": _plan(),
        "plan_first": True,
        "shot_floor_policy": policy,
        # 注意：`resolve_shot_params` 只读 extra_params.shot_params（已合并的剂量值），
        # 顶层 min_shot_ms 会被忽略——第一版就是这么"什么都没发生"的
        "shot_params": {"base_shot_ms": 5000, "min_shot_ms": FLOOR_MS, "max_shot_ms": 20000},
    }
    res = client.post("/api/pipeline/run-async", json={
        "persona_id": "default",
        "category_plugin_id": "vlog_daily",
        "topic": topic,
        "dry_run": True,
        "extra_params": extra,
    })
    if res.status_code >= 400:
        return {"ok": False, "error": res.text[:300], "policy": policy}
    pipeline_id = str((res.json() or {}).get("pipeline_id") or "")
    status = ""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        status = str(client.get(f"/api/pipeline/status/{pipeline_id}").json().get("status") or "")
        if status.lower() in TERMINAL:
            break
        time.sleep(interval)

    events = client.get(f"/api/pipeline/trace/{pipeline_id}").json()
    if not isinstance(events, list):
        events = []
    snapshot = next(
        (e.get("detail") for e in events if e.get("type") == "timeline_snapshot"), None
    ) or {}
    clips: list[float] = []
    for track in snapshot.get("tracks") or []:
        if track.get("kind") != "video":
            continue
        clips += [round(float(c.get("duration_sec") or 0), 3) for c in (track.get("clips") or [])]
    floor_event = next(
        (e.get("detail") for e in events if e.get("type") == "shot_floor"), None
    )
    dump = ROOT / "out" / "d23" / f"trace-{pipeline_id}.json"
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": status.lower() == "completed",
        "policy": policy,
        "pipeline_id": pipeline_id,
        "status": status,
        "clip_durations_sec": clips,
        "min_clip_sec": min(clips) if clips else None,
        "all_clips_at_or_above_floor": bool(clips) and all(c * 1000 >= FLOOR_MS - 1 for c in clips),
        "timeline_duration_sec": snapshot.get("duration_sec"),
        "shot_floor_event": floor_event,
        "trace_dump": str(dump.relative_to(ROOT)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D23：镜头下限策略真机对照")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    started = time.perf_counter()
    with TestClient(app) as client:
        record = _run(client, policy="record", topic="下限对照·仅记录",
                      timeout=args.timeout, interval=args.interval)
        extend = _run(client, policy="extend", topic="下限对照·抬到下限",
                      timeout=args.timeout, interval=args.interval)

    report = {
        "ok": bool(record.get("ok") and extend.get("ok")),
        "floor_ms": FLOOR_MS,
        "record": record,
        "extend": extend,
        "conclusion": {
            "record_conflicts": len((record.get("shot_floor_event") or {}).get("conflicts") or []),
            "record_min_clip_sec": record.get("min_clip_sec"),
            "extend_min_clip_sec": extend.get("min_clip_sec"),
            "extend_meets_floor": extend.get("all_clips_at_or_above_floor"),
            "duration_change_sec": (
                round((extend.get("timeline_duration_sec") or 0)
                      - (record.get("timeline_duration_sec") or 0), 3)
            ),
        },
        "caveat": "默认策略仍是 off；本报告只是把 D23 的两种候选跑成可比较的数字",
        "elapsed_sec": round(time.perf_counter() - started, 1),
    }
    target = ROOT / "out" / "d23" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
