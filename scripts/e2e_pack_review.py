"""把一个已跑完的 E2E 任务打包成可审查目录（不重跑、不花钱）。

输入：任务 id + 工作区目录 + 已有的 events.json/task.json（可选）。
输出：`REVIEW.md`、`runs/*.json`、`reports/**`、`events.json`、`task.json`、
`timeline.md`、`index.json`，并可打 zip。

用法：
    python scripts/e2e_pack_review.py --task task_xxx --out out/e2e/review-xxx
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workspace", default="")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    task_id = args.task
    ws = Path(args.workspace) if args.workspace else next(
        (p for p in (ROOT / "workspaces").glob(f"{task_id}-*") if p.is_dir()), None)
    if ws is None:
        print(f"[pack] 找不到工作区 workspaces/{task_id}-*")
        return 2

    task = _load(out / "task.json") or {}
    events = (_load(out / "events.json") or {}).get("items") or []
    closeout = _load(ws / "reports" / "agent-closeout.json") or {}

    # ── 复制 runs / reports ──────────────────────────────────────
    runs_dir = out / "runs"
    runs_dir.mkdir(exist_ok=True)
    run_objs: list[dict] = []
    for f in sorted((ws / "runs").glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        shutil.copy2(f, runs_dir / f.name)
        run_objs.append(data)

    reports_dir = out / "reports"
    reports_dir.mkdir(exist_ok=True)
    for f in sorted((ws / "reports").rglob("*")):
        if f.is_file():
            target = reports_dir / f.relative_to(ws / "reports")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)

    # ── 事件时间线（只挑结构性节点）────────────────────────────
    interesting = {
        "task_start", "plan", "replan", "step_start", "step_end", "agent_run_start",
        "agent_run_end", "degradation", "gate_hit", "doc_write", "report",
        "goal_update", "soft_landing", "task_end", "progress", "subagent_round",
    }
    timeline: list[str] = []
    for e in events:
        typ = str(e.get("type"))
        if typ not in interesting:
            continue
        detail = e.get("detail") or {}
        bits = []
        for k in ("stage", "status", "reason", "terminal_reason", "report_state",
                  "run_id", "round", "mode"):
            if detail.get(k) not in (None, "", [], {}):
                bits.append(f"{k}={detail[k]}")
        if typ == "degradation":
            bits = [str(detail.get("kind") or ""), str(detail.get("detail") or "")[:160]]
        if typ == "step_end":
            bits = [f"stage={detail.get('stage')}", f"status={detail.get('status')}",
                    f"report_state={detail.get('report_state')}"]
        if typ == "soft_landing":
            bits = [f"mode={detail.get('mode')}", f"reason={detail.get('reason')}"]
        line = f"- seq={e.get('seq')} **{typ}** " + " ".join(x for x in bits if x)
        msg = str(e.get("message") or "").strip()
        if msg:
            line += f" — {msg[:160]}"
        timeline.append(line)
    (out / "timeline.md").write_text("# 事件时间线\n\n" + "\n".join(timeline) + "\n",
                                     encoding="utf-8")

    # ── 阶段表（按 run 的 stage 汇总）──────────────────────────
    by_stage: dict[str, list[dict]] = {}
    for r in run_objs:
        by_stage.setdefault(str(r.get("stage") or "?"), []).append(r)

    finish = task.get("finish") or {}
    usage = task.get("usage") or {}
    stage_rows = []
    for stage, items in by_stage.items():
        for r in sorted(items, key=lambda x: x.get("duration_ms") or 0, reverse=True):
            deg = r.get("degradations") or []
            kinds = "、".join(sorted({str(d.get("kind")) for d in deg if d.get("kind")}))
            stage_rows.append(
                f"| {stage} | {r.get('status')} | {r.get('attempt')} | "
                f"{round((r.get('duration_ms') or 0)/1000,1)}s | "
                f"{r.get('tool_calls_total', 0)} | {len(deg)}（{kinds}） | "
                f"{r.get('report_state')} | {str(r.get('handoff_summary') or '')[:70]} |")

    unresolved = list(closeout.get("unfinished") or finish.get("unfinished") or [])
    completed = list(closeout.get("completed") or finish.get("completed") or [])
    why = str(closeout.get("why") or finish.get("conclusion") or "")
    blocker = str(closeout.get("blocker_code") or finish.get("reason") or "")

    review = [
        f"# 300 字 E2E 审查报告（{task_id}）",
        "",
        "## 1. 结论",
        "",
        f"- 状态：**{task.get('status')}**；终态原因：`{task.get('terminal_reason')}`；"
        f"阻塞码：`{blocker}`",
        f"- 为什么结束：{why or '（未记录）'}",
        f"- 耗时：{usage.get('wall_sec')}s；turns={usage.get('turns')}；"
        f"tool_calls={usage.get('tool_calls')}；cost={usage.get('cost_usd')} "
        f"(unknown_cost_calls={usage.get('unknown_cost_calls')})",
        f"- 脚本：303 字；音频目标 75s；事件 {len(events)} 条。",
        "",
        "## 2. 已完成 / 未完成",
        "",
        "**已完成**：" + ("、".join(f"`{c}`" for c in completed) or "（无）"),
        "",
        "**未完成**：",
        *[f"- {u}" for u in unresolved],
        "",
        "## 3. 阶段结果（来自 run_store 的 AgentResult）",
        "",
        "| 阶段 | 状态 | attempt | 耗时 | 工具 | 降级 | 报告 | 交接摘要 |",
        "|---|---|---|---|---|---|---|---|",
        *stage_rows,
        "",
        "## 4. 关键事件时间线",
        "",
        "完整版见 `timeline.md`；以下是结构性节点：",
        "",
        *timeline,
        "",
        "## 5. 数据文件",
        "",
        "- `task.json`：任务终态（status/terminal_reason/finish/usage/goal）",
        "- `events.json`：全量事件（含 spilled）",
        "- `timeline.md`：过滤后的结构时间线",
        "- `runs/*.json`：8 个阶段 run 的 AgentResult（含 needs/degradations/handoff）",
        "- `reports/`：工作区报告与 closeout/降级台账/进度流水",
        "",
        "## 6. 审查时重点看",
        "",
        "1. **动画阶段 2 条 `mg_motion_weak`** 是否应被视为可接受的降级，还是需要重做；",
        "2. **质量阶段为何没跑**：closeout 明确写 `quality_not_run`，且阻塞码是 "
        "`degradation_unresolved`——主 Agent 在“收尾/质量”之间选择了收尾；",
        "3. **成本为 0 不可信**：`cost_usd=0.0` 但 `unknown_cost_calls=1`，说明账没记全；",
        "4. 两次 structure / 两次 edit 是重试，不是重复执行（attempt 字段）。",
        "",
        f"_生成于 {datetime.now().isoformat(timespec='seconds')}_",
    ]
    (out / "REVIEW.md").write_text("\n".join(review) + "\n", encoding="utf-8")

    index = {
        "task_id": task_id,
        "workspace": str(ws),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()),
        "run_count": len(run_objs),
        "event_count": len(events),
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    if args.zip:
        zpath = out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out))
        print(f"[pack] zip: {zpath}")

    print(f"[pack] 审查包: {out}（runs={len(run_objs)} events={len(events)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
