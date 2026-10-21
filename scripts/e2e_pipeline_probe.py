"""端到端探针：跑一次真实管线（dry-run），抓「创作提示词是否进入执行链」的证据。

做四件事：
1. 用 TestClient 启动 `/api/pipeline/run-async`，带 `creative_prompt`；
2. 轮询状态直到终态或超时；
3. 拉 trace，统计 `prompt_resolution` 事件与结构阶段事件；
4. 产出 `out/e2e/report.json`（状态、耗时、事件计数、是否看到裁决）。

**口径**：dry-run 不出片，所以它只证明"链路上跑通并留痕"，不证明成片质量。

用法::

    python scripts/e2e_pipeline_probe.py
    python scripts/e2e_pipeline_probe.py --category knowledge_longform --timeout 600
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

DEFAULT_PROMPT = "版画质感，三墨套印，硬切为主；不要花哨转场。"

PROBE_CATEGORY = "probe_floor"

TERMINAL = {"completed", "failed", "cancelled", "canceled"}


def _register_probe_category(floor_ms: int = 1200, max_ms: int = 20000) -> None:
    """注册一个**只存在于探针进程**的临时类型：声明 min_shot_ms 严格下限 1200。

    为什么要在真实管线里造这个类型：四个内置插件都没声明 ``param_rules``
    （基类默认空），所以用内置类型跑真机永远触发不了 V2 的 strict floor。
    这里注入一个探针专用类型，才能证明"用户在真实管线里给出越界值 → 被类型
    规则改写并留痕"，且不污染产品目录（进程结束即消失）。
    """
    from clipwright.category.base import BaseCategoryPlugin
    from clipwright.category.registry import CategoryRegistry
    from clipwright.schema.category_spec import ParamRule, PromptBundle, PromptSlot

    class _ProbeFloorPlugin(BaseCategoryPlugin):
        plugin_id = PROBE_CATEGORY
        display_name = "探针·严格下限"
        description = "e2e 探针专用临时类型（不写入产品目录）"

        def translate_persona(self, params):  # noqa: ANN001, ANN201 - 基类签名
            return {
                "shot_params": {"base_shot_ms": 5000, "min_shot_ms": 300, "max_shot_ms": max_ms},
                "cut_profile": "even_flow",
                "transition_weights": {"hard_cut": 1.0},
                "annotation_templates": ["hook"],
            }

        def get_shot_params(self, translated):  # noqa: ANN001, ANN201
            return translated.get("shot_params", {})

        def get_param_rules(self):  # noqa: ANN201
            return [ParamRule(key="min_shot_ms", mode="floor", value=floor_ms,
                              enforce="strict", reason="探针：验证 strict 下限在真机链路生效")]

        def get_prompt_bundle(self):  # noqa: ANN201
            return PromptBundle(slots={
                "genre": PromptSlot(text="探针类型：版画质感，三墨套印，硬切为主。", priority=90),
            })

    if CategoryRegistry.get(PROBE_CATEGORY) is None:
        CategoryRegistry.register(_ProbeFloorPlugin())


def _probe_plan() -> dict:
    """构造一份**合法但与执行必然偏离**的规划书（V58 端到端取证用）。

    规划段落连续 0→20s、画面类型声明为 plate；结构阶段实际产出的场景时长/数量
    几乎必然不同 → `detect_drift` 应报 `plan_drift_duration` 与
    `plan_drift_section_count`，且这批偏离要同时出现在 trace 里。
    """
    return {
        "type": "vlog_daily",
        "target_duration_sec": 20.0,
        "sections": [
            {"id": "A", "name": "开场", "t0": 0.0, "t1": 6.0, "purpose": "建立场景",
             "screen_kinds": ["plate"]},
            {"id": "B", "name": "主体", "t0": 6.0, "t1": 20.0, "purpose": "展开论证",
             "screen_kinds": ["plate"]},
        ],
        "screen_kinds": ["plate"],
        "material_strategy": {"gaps": ["手部特写 x2"]},
        "legacy": False,
    }


def _parse_overrides(pairs: list[str] | None) -> dict:
    """``--set min_shot_ms=300`` → ``{"min_shot_ms": 300}``（数字/布尔自动转）。"""
    out: dict = {}
    for raw in pairs or []:
        if "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        text = val.strip()
        lowered = text.lower()
        if lowered in {"true", "false"}:
            out[key.strip()] = lowered == "true"
            continue
        try:
            out[key.strip()] = int(text)
            continue
        except ValueError:
            pass
        try:
            out[key.strip()] = float(text)
            continue
        except ValueError:
            out[key.strip()] = text
    return out


def _summarize_resolution(events: list) -> dict:
    """抽出第一条 prompt_resolution 的骨架（不全文抄，避免报告难读）。"""
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "prompt_resolution":
            continue
        detail = event.get("detail") or {}
        params = detail.get("params") or {}
        return {
            "agent": event.get("agent"),
            "category_id": detail.get("category_id"),
            "stage": detail.get("stage"),
            "param_count": len(params),
            "params": {
                key: {"value": val.get("value"), "source_layer": val.get("source_layer")}
                for key, val in list(params.items())[:8]
            },
            "conflicts": [
                {"code": c.get("code"), "key": c.get("key"), "enforce": c.get("enforce")}
                for c in (detail.get("conflicts") or [])
            ],
            "slot_count": len(detail.get("slots") or {}),
            "dropped_slots": detail.get("dropped_slots") or [],
            "deformation": detail.get("deformation"),
        }
    return {}


def _summarize_timeline(events: list) -> dict:
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "timeline_snapshot":
            continue
        detail = event.get("detail") or {}
        tracks = detail.get("tracks") or []
        per_track = []
        assets: list[str] = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            clips = track.get("clips") or []
            per_track.append({"name": track.get("name"), "kind": track.get("kind"), "clips": len(clips)})
            assets += [str(c.get("asset_id") or "") for c in clips if isinstance(c, dict)]
        return {
            "width": detail.get("width"),
            "height": detail.get("height"),
            "fps": detail.get("fps"),
            "duration_sec": detail.get("duration_sec"),
            "track_count": len(tracks),
            "per_track": per_track,
            "clip_total": len(assets),
            "asset_sample": assets[:5],
            "all_placeholder": bool(assets) and all("textvid" in a or not a for a in assets),
        }
    return {}


def _video_durations(events: list) -> list[float]:
    """取时间线视频轨每段的时长（秒）——V2「压到成片上」要看的就是这个。"""
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "timeline_snapshot":
            continue
        out: list[float] = []
        for track in (event.get("detail") or {}).get("tracks") or []:
            if not isinstance(track, dict) or track.get("kind") != "video":
                continue
            for clip in track.get("clips") or []:
                if isinstance(clip, dict):
                    try:
                        out.append(float(clip.get("duration_sec") or 0.0))
                    except (TypeError, ValueError):
                        continue
        return out
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实管线 dry-run 探针")
    parser.add_argument("--topic", default="手机续航实测对比")
    parser.add_argument("--category", default="vlog_daily")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--scenario", choices=["plain", "floor"], default="plain",
                        help="plain=内置类型直跑；floor=注入带严格下限的探针类型")
    parser.add_argument("--floor", type=int, default=1200,
                        help="--scenario floor 时的严格下限（毫秒），用于检验是否能压到成片时间线上")
    parser.add_argument("--max-ms", type=int, default=20000,
                        help="探针类型的 max_shot_ms（毫秒）；要与 --floor 组成合法区间才能真正检验下限")
    parser.add_argument("--set", dest="overrides", action="append", metavar="KEY=VALUE",
                        help="额外塞进 extra_params 的取值，可重复（如 --set min_shot_ms=300）")
    parser.add_argument("--with-plan", action="store_true",
                        help="注入一份合法规划书并开 plan_first，验证执行偏离是否进 trace（V58）")
    parser.add_argument("--writeback", action="store_true",
                        help="建真实需求会话 + 规划，跑完回读会话验证偏离是否写回规划书（V58 写回半边）")
    parser.add_argument("--full", action="store_true",
                        help="不做 dry-run：真跑动画/音频/质检/渲染（慢，用于取 render_sec 与成片）")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    report: dict = {
        "ok": False,
        "topic": args.topic,
        "category": args.category,
        "creative_prompt": args.prompt,
    }
    started = time.perf_counter()
    extra: dict = {"creative_prompt": args.prompt, "creative_avoid": ["花哨转场"]}
    extra.update(_parse_overrides(args.overrides))
    if args.with_plan:
        extra["plan"] = _probe_plan()
        extra["plan_first"] = True
        extra["plan_drift_path"] = str(ROOT / "out" / "e2e" / "plan_drift.jsonl")
        report["scenario"] = "plan_drift"
        report["injected_plan"] = {"target_duration_sec": 20.0, "sections": ["A 0-6s", "B 6-20s"]}
    if args.scenario == "floor":
        _register_probe_category(args.floor, args.max_ms)
        args.category = PROBE_CATEGORY
        report["category"] = PROBE_CATEGORY
        report["scenario"] = "floor"
        report["injected_rules"] = [{"key": "min_shot_ms", "mode": "floor", "value": args.floor, "enforce": "strict"}]
    report["extra_params"] = extra
    report["dry_run"] = not args.full

    with TestClient(app) as client:
        if args.writeback:
            # V58 写回：先建一个真实需求会话并把规划写进去（模拟"规划已确认"），
            # 再让管线带上 session_id 跑，最后回读会话看规划书有没有被写回。
            init_res = client.post("/api/requirements/init", json={
                "topic": args.topic,
                "persona_id": args.persona,
                "category_plugin_id": args.category,
            })
            session_id = str((init_res.json() or {}).get("session_id") or "")
            report["session_id"] = session_id
            report["session_init_http"] = init_res.status_code
            if not session_id:
                report["error"] = f"会话创建失败: {init_res.text[:300]}"
                _write(report, started)
                return 1
            from clipwright.api.requirements import _service

            _service._persist(
                session_id, "plan_confirmed", [],
                {"overview": "探针简报", "core_message": "版画质感"},
                _probe_plan(), {},
            )
            extra["session_id"] = session_id
            report["scenario"] = "writeback"

        payload = {
            "persona_id": args.persona,
            "category_plugin_id": args.category,
            "topic": args.topic,
            "dry_run": not args.full,
            "extra_params": extra,
        }
        res = client.post("/api/pipeline/run-async", json=payload)
        report["start_http"] = res.status_code
        if res.status_code >= 400:
            report["error"] = res.text[:500]
            _write(report, started)
            return 1
        pipeline_id = str((res.json() or {}).get("pipeline_id") or "")
        report["pipeline_id"] = pipeline_id
        if not pipeline_id:
            report["error"] = f"未返回 pipeline_id: {res.text[:300]}"
            _write(report, started)
            return 1

        status = ""
        deadline = time.perf_counter() + args.timeout
        while time.perf_counter() < deadline:
            sres = client.get(f"/api/pipeline/status/{pipeline_id}")
            data = sres.json() if sres.status_code < 400 else {}
            status = str(data.get("status") or "")
            if status.lower() in TERMINAL:
                break
            time.sleep(args.interval)
        report["status"] = status

        tres = client.get(f"/api/pipeline/trace/{pipeline_id}")
        events = tres.json() if tres.status_code < 400 else []
        if not isinstance(events, list):
            events = []
        types: dict[str, int] = {}
        for event in events:
            if isinstance(event, dict):
                key = str(event.get("type") or "unknown")
                types[key] = types.get(key, 0) + 1
        report["event_count"] = len(events)
        report["event_types"] = types
        dump = ROOT / "out" / "e2e" / f"trace-{pipeline_id}.json"
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        report["trace_dump"] = str(dump.relative_to(ROOT))
        report["resolution"] = _summarize_resolution(events)
        report["timeline"] = _summarize_timeline(events)
        if args.scenario == "floor":
            floor_sec = args.floor / 1000.0
            durations = _video_durations(events)
            report["floor_check"] = {
                "floor_ms": args.floor,
                "video_clip_count": len(durations),
                "min_clip_ms": round(min(durations) * 1000) if durations else None,
                "max_clip_ms": round(max(durations) * 1000) if durations else None,
                "all_clips_at_or_above_floor": bool(durations) and all(d >= floor_sec for d in durations),
            }
        report["prompt_resolution_events"] = types.get("prompt_resolution", 0)
        report["plan_drift_events"] = types.get("plan_drift", 0)
        if args.writeback:
            sess = client.get(f"/api/requirements/session/{report['session_id']}")
            payload = sess.json() if sess.status_code < 400 else {}
            plan = (payload or {}).get("production_plan") or {}
            drift = plan.get("_drift") if isinstance(plan, dict) else None
            report["writeback"] = {
                "session_http": sess.status_code,
                "plan_has_drift": bool(drift),
                "drift_count": len(drift or []),
                "drift_codes": sorted({str(d.get("code")) for d in (drift or []) if isinstance(d, dict)}),
                "plan_body_kept": bool(isinstance(plan, dict) and plan.get("sections")),
            }
        report["agents_seen"] = sorted(
            {
                str(e.get("agent"))
                for e in events
                if isinstance(e, dict) and e.get("agent")
            }
        )
        report["ok"] = status.lower() == "completed" and types.get("prompt_resolution", 0) > 0

    _write(report, started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write(report: dict, started: float) -> None:
    report["elapsed_sec"] = round(time.perf_counter() - started, 1)
    name = f"report-{report.get('scenario') or 'plain'}.json"
    target = ROOT / "out" / "e2e" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
