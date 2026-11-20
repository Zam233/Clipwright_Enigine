"""300 字真实 E2E：跑一次主 Agent + 阶段子 Agent 全流程，并落审查包。

产出目录：``out/e2e/review-300-<timestamp>/``
  - summary.md            人读的流程摘要（阶段/run/事件/成本）
  - task.json             任务终态
  - events.json           全量事件（含 spilled）
  - runs/<run_id>.json    每个 run 的 AgentResult
  - tool_calls/<run_id>.json
  - reports/*.md          工作区报告原文
  - workspace_tree.txt    工作区文件清单

用法（工作目录 J:\\Clipwright）：
    python scripts/e2e_300_review.py
    python scripts/e2e_300_review.py --timeout 2400
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = (
    "很多人以为学文科就是背书写文章，学理科就是刷题算公式。"
    "但真正的差别不在知识本身，而在你面对一个问题时先做什么。"
    "理科训练你先定义变量、找约束、做可重复的实验；"
    "文科训练你先追问语境、看谁在说话、这句话对谁有利。"
    "一个只懂公式的人，遇到没有标准答案的问题会慌；"
    "一个只会引用的人，遇到需要证据的问题也会慌。"
    "所以文理之争从来不是谁更有用，而是两种处理不确定性的方法。"
    "更现实地说，工作里大多数难题都同时需要两边：先用文科的方式把问题说清楚，"
    "再用理科的方式把它拆成可以验证的小步骤，最后还要能向别人解释你为什么这样做。"
    "把两种方法都练一遍，你才可能在真实世界里少交学费。"
    "这也是为什么真正厉害的人，往往既能写清楚，也能算明白。"
)

TERMINAL = {"done", "failed", "cancelled", "capped"}


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(str(data), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--poll", type=float, default=5.0)
    args = ap.parse_args()

    from fastapi.testclient import TestClient

    from clipwright.main import app

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "out" / "e2e" / f"review-300-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[e2e] 输出目录: {out_dir}", flush=True)

    with TestClient(app) as client:
        body = {
            "topic": "文理之争：两种处理不确定性的方法",
            "persona_id": "zam_knowledge_critical",
            "category_plugin_id": "knowledge_longform",
            "duration_sec": 75,
            "plan_only": False,
            "extra": {
                "script_text": SCRIPT,
                "audio_duration_sec": 75,
                "video_mode": "voiceover",
            },
        }
        r = client.post("/api/task", json=body)
        if r.status_code >= 400:
            print("[e2e] 创建任务失败:", r.status_code, r.text[:800], flush=True)
            return 2
        created = r.json()
        tid = created["task"]["id"]
        print(f"[e2e] task={tid} started={created.get('started')}", flush=True)

        started = time.time()
        last_status = ""
        last_events = -1
        final: dict = {}
        while True:
            if time.time() - started > args.timeout:
                print("[e2e] 超时，抓取当前状态", flush=True)
                break
            tr = client.get(f"/api/task/{tid}")
            if tr.status_code != 200:
                print("[e2e] 取任务失败", tr.status_code, tr.text[:400], flush=True)
                time.sleep(args.poll)
                continue
            task = tr.json()["task"]
            status = str(task.get("status") or "")
            ev_count = int(tr.json().get("events") or 0)
            if status != last_status or ev_count != last_events:
                usage = task.get("usage") or {}
                print(f"[e2e] {int(time.time()-started):4d}s status={status} "
                      f"events={ev_count} turns={usage.get('turns')} "
                      f"tools={usage.get('tool_calls')} cost={usage.get('cost_usd')}", flush=True)
                last_status, last_events = status, ev_count
            final = task
            if status in TERMINAL:
                break
            time.sleep(args.poll)

        # ── 抓全量数据 ─────────────────────────────────────────────
        _dump(out_dir / "task.json", final)
        try:
            ev = client.get(f"/api/task/{tid}/events", params={"limit": 5000})
            _dump(out_dir / "events.json", ev.json() if ev.status_code == 200 else {"error": ev.text})
        except Exception as exc:  # noqa: BLE001
            _dump(out_dir / "events.json", {"error": str(exc)})

        runs: list[dict] = []
        try:
            rr = client.get(f"/api/task/{tid}/runs")
            runs = list((rr.json() or {}).get("items") or (rr.json() or {}).get("runs") or [])
            _dump(out_dir / "runs_list.json", rr.json())
            for item in runs:
                rid = str(item.get("run_id") or "")
                if not rid:
                    continue
                rd = client.get(f"/api/task/{tid}/runs/{rid}")
                if rd.status_code == 200:
                    _dump(out_dir / "runs" / f"{rid.replace(':', '_')}.json", rd.json())
                tc = client.get(f"/api/task/{tid}/tool-calls", params={"run_id": rid})
                if tc.status_code == 200:
                    _dump(out_dir / "tool_calls" / f"{rid.replace(':', '_')}.json", tc.json())
        except Exception as exc:  # noqa: BLE001
            _dump(out_dir / "runs_list.json", {"error": str(exc)})

        # 工作区报告/文件清单（同一进程，直接读 workspace）
        root = ""
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(tid)
            root = str(ws.dirs().root) if ws.exists else ""
        except Exception:  # noqa: BLE001
            root = str((((final.get("request") or {}).get("extra") or {}).get("workspace_root") or ""))
        if not root:
            # 直接建任务时 extra 可能没有 workspace_root；内核工作区在 workspaces/<id>-*
            cands = [p for p in (ROOT / "workspaces").glob(f"{tid}-*") if p.is_dir()]
            root = str(cands[0]) if cands else ""
        if root:
            base = Path(root)
            tree = [str(p.relative_to(base)) for p in sorted(base.rglob("*")) if p.is_file()]
            _dump(out_dir / "workspace_tree.txt", "\n".join(tree))
            for p in sorted(base.rglob("reports/*.md")):
                rel = p.relative_to(base)
                target = out_dir / "reports" / str(rel).replace("\\", "/")
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    text = p.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001
                    continue
                target.write_text(text[:300_000], encoding="utf-8")

        status = str(final.get("status") or "")
        reason = str(final.get("terminal_reason") or (final.get("finish") or {}).get("reason") or "")
        usage = final.get("usage") or {}
        ev_items = []
        try:
            ev_items = (json.loads((out_dir / "events.json").read_text(encoding="utf-8")) or {}).get("items") or []
        except Exception:  # noqa: BLE001
            pass
        counts: dict[str, int] = {}
        for e in ev_items:
            counts[str(e.get("type"))] = counts.get(str(e.get("type")), 0) + 1

        lines = [
            f"# 300 字 E2E 流程审查包（{stamp}）",
            "",
            f"- task_id: `{tid}`",
            f"- 状态: **{status}**" + (f"（{reason}）" if reason else ""),
            f"- 耗时: 约 {int(time.time()-started)}s",
            f"- 轮次/工具/成本: turns={usage.get('turns')} tool_calls={usage.get('tool_calls')} "
            f"cost_usd={usage.get('cost_usd')}",
            f"- 脚本字数: {len(SCRIPT)}",
            f"- 事件数: {len(ev_items)}",
            "",
            "## 事件类型计数",
            "",
            *[f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
            "",
            "## 子 Agent runs",
            "",
            "| run_id | stage | status | attempt | tool_calls | cost | report |",
            "|---|---|---|---|---|---|---|",
        ]
        for it in runs:
            lines.append(
                f"| {it.get('run_id')} | {it.get('stage')} | {it.get('status')} | {it.get('attempt')} "
                f"| {it.get('tool_calls_total', it.get('tool_calls'))} | {it.get('cost_usd')} "
                f"| {it.get('report_path') or ''} |"
            )
        lines += [
            "",
            "## 产物",
            "",
            "- `task.json` / `events.json` / `runs_list.json`",
            "- `runs/<run_id>.json`：每个子 Agent 的 AgentResult（needs/redo/degradations/报告摘要）",
            "- `tool_calls/<run_id>.json`：按 run 的全量工具轨迹",
            "- `reports/**`：工作区报告原文（自由 Markdown）",
            "- `workspace_tree.txt`：工作区文件清单",
            "",
            "## 如何审查",
            "",
            "1. `task.json → finish/terminal_reason/usage`：怎么结束、花了多少；",
            "2. `events.json → items`：按 seq 看规划、派发、工具、阶段收据（`agent_run_*`）；",
            "3. `runs/*.json`：每个阶段是否 `ok/degraded/failed`、`needs` 与 `redo_request`；",
            "4. `tool_calls/*.json`：模型到底调了什么、失败在哪一步；",
            "5. `reports/*.md`：人读汇报（机器事实以 AgentResult 为准）。",
        ]
        (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[e2e] 完成: status={status} runs={len(runs)} events={len(ev_items)}", flush=True)
        print(f"[e2e] 审查包: {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
