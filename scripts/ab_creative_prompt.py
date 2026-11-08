"""V45 的 A/B：创作提示词到底改了什么（量化差异，不是"留痕了"）。

做法：同一主题、同一类型、同一份规划，跑两次 dry-run 管线——

- **A 组（对照）**：不带创作提示词；
- **B 组（实验）**：带"版画质感，三墨套印，硬切为主；不要花哨转场"。

然后比对两条时间线：转场类型分布、镜头时长分布、动画/文字轨结构。
**口径**：n=1 的对照，只说明"这一次差在哪"，不做统计显著性声明；dry-run 不出片，
所以这是"提示词 → 时间线决策"的证据，不是"提示词 → 画面质量"的证据。

用法::

    python scripts/ab_creative_prompt.py
    python scripts/ab_creative_prompt.py --category vlog_daily --timeout 480
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROMPT = "版画质感，三墨套印，硬切为主；不要花哨转场。"
TERMINAL = {"completed", "failed", "cancelled", "canceled"}


def _plan() -> dict:
    """同一份规划给两组用，避免"规划不同"污染对照。"""
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
        "material_strategy": {"gaps": ["手部特写 x2"]},
        "legacy": False,
    }


def _run_once(client, *, topic: str, category: str, persona: str, prompt: str,
              timeout: float, interval: float) -> dict:
    extra: dict = {"plan": _plan(), "plan_first": True}
    if prompt:
        extra["creative_prompt"] = prompt
        extra["creative_avoid"] = ["花哨转场"]
    res = client.post("/api/pipeline/run-async", json={
        "persona_id": persona,
        "category_plugin_id": category,
        "topic": topic,
        "dry_run": True,
        "extra_params": extra,
    })
    if res.status_code >= 400:
        return {"ok": False, "error": res.text[:400]}
    pipeline_id = str((res.json() or {}).get("pipeline_id") or "")
    status = ""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        data = client.get(f"/api/pipeline/status/{pipeline_id}").json()
        status = str(data.get("status") or "")
        if status.lower() in TERMINAL:
            break
        time.sleep(interval)
    events = client.get(f"/api/pipeline/trace/{pipeline_id}").json()
    if not isinstance(events, list):
        events = []
    _dump(events, pipeline_id)
    return {
        "ok": status.lower() == "completed",
        "pipeline_id": pipeline_id,
        "status": status,
        "prompt_used": bool(prompt),
        "metrics": _metrics(events),
        "prompt_resolution": next(
            (e.get("detail") for e in events if e.get("type") == "prompt_resolution"), None
        ),
    }


def _dump(events: list, pipeline_id: str) -> None:
    target = ROOT / "out" / "ab" / f"trace-{pipeline_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def _metrics(events: list) -> dict:
    """从 trace 里的 timeline_snapshot 抽可比指标（实现见 tools/creative_ab.py）。"""
    from tools.creative_ab import timeline_metrics

    snapshot = next(
        (e.get("detail") for e in events if e.get("type") == "timeline_snapshot"), None
    )
    return timeline_metrics(snapshot)


def _diff(a: dict, b: dict) -> dict:
    from tools.creative_ab import diff_metrics

    return diff_metrics(a.get("metrics") or {}, b.get("metrics") or {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V45：创作提示词的 A/B 对照")
    parser.add_argument("--topic", default="手机续航实测对比")
    parser.add_argument("--category", default="vlog_daily")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--pairs", type=int, default=1,
                        help="跑几对（对照组+实验组算一对）；>1 用来看节奏指标的方向稳不稳")
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    started = time.perf_counter()
    pairs: list[dict] = []
    with TestClient(app) as client:
        for index in range(max(1, args.pairs)):
            control = _run_once(client, topic=args.topic, category=args.category,
                                persona=args.persona, prompt="",
                                timeout=args.timeout, interval=args.interval)
            prompted = _run_once(client, topic=args.topic, category=args.category,
                                 persona=args.persona, prompt=PROMPT,
                                 timeout=args.timeout, interval=args.interval)
            pairs.append({
                "pair": index + 1,
                "control": control,
                "prompted": prompted,
                "delta": _diff(control, prompted),
            })

    control = pairs[0]["control"]
    prompted = pairs[0]["prompted"]
    report = {
        "ok": all(p["control"].get("ok") and p["prompted"].get("ok") for p in pairs),
        "prompt": PROMPT,
        "category": args.category,
        "pairs": pairs,
        "control": control,
        "prompted": prompted,
        "delta": pairs[0]["delta"],
        "elapsed_sec": round(time.perf_counter() - started, 1),
        "caveat": (
            "dry-run 不出片，本报告只说明提示词对时间线决策的影响，不构成画面质量证据；"
            "节奏类指标受 LLM 采样影响，多轮结果可能不同向，故逐对列出而不是取平均来"
            "掩盖方差"
        ),
    }
    target = ROOT / "out" / "ab" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 历史留档：每次跑完追加一行摘要，便于比较多次采样的方向是否稳定
    history = target.parent / "history.jsonl"
    with history.open("a", encoding="utf-8") as handle:
        for pair in pairs:
            handle.write(json.dumps({
                "pair": pair["pair"],
                "control_keyword_total": (pair["control"].get("metrics") or {}).get("keyword_total"),
                "prompted_keyword_total": (pair["prompted"].get("metrics") or {}).get("keyword_total"),
                "control_mean_clip_sec": (pair["control"].get("metrics") or {}).get("mean_clip_sec"),
                "prompted_mean_clip_sec": (pair["prompted"].get("metrics") or {}).get("mean_clip_sec"),
                "control_video_clips": (pair["control"].get("metrics") or {}).get("video_clips"),
                "prompted_video_clips": (pair["prompted"].get("metrics") or {}).get("video_clips"),
            }, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
