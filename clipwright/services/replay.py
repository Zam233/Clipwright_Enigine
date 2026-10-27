"""Bundle 回放与校验（closeout B10 / modern-agent PRD §5.5）。

`python -m clipwright.services.replay --bundle PATH [--json]`

**它回放什么、不回放什么，必须说清楚**：

- 回放的是**事件投影**：把 bundle 里的 `events` 重新走一遍投影（序号单调、工具调用与
  结果配对、子 run 有始有终），据此判断"这份证据是否自洽、能否复盘因果"；
- **不**重跑模型、**不**重跑工具——那需要录制回放（`services.recording`）与真实 provider，
  属于另一条路（golden harness 走的就是录制回放）。

为什么仍值得做：失败样本复盘的第一步不是"再跑一次"，而是"先把已有证据查一遍"。
这一层能在几毫秒内回答"这份 bundle 到底缺了什么"，而重跑要花几分钟和真金白银。

退出码：0 = 自洽；1 = 发现缺口（缺 events / 缺 run 结果 / 序号断裂 / 工具结果无因果）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

#: bundle 必须包含的四段（与 `evaluation.replay_bundle` 产物一致）
REQUIRED_SECTIONS = ("task_id", "events", "runs", "metrics")


class BundleError(ValueError):
    """bundle 结构不合法（不是"任务失败"，而是"证据本身不成立"）。"""


def load_bundle(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise BundleError(f"bundle 不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 坏 JSON 要如实报
        raise BundleError(f"bundle 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleError("bundle 顶层必须是对象")
    missing = [k for k in REQUIRED_SECTIONS if k not in data]
    if missing:
        raise BundleError(f"bundle 缺少字段: {missing}")
    return data


def _seq_of(ev: dict[str, Any]) -> int:
    try:
        return int(ev.get("seq"))
    except Exception:  # noqa: BLE001
        return -1


def check_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """校验一份 bundle 的自洽性；返回 `{"ok": bool, "issues": [...], "stats": {...}}`。"""
    issues: list[str] = []
    events = [e for e in (bundle.get("events") or []) if isinstance(e, dict)]
    runs = [r for r in (bundle.get("runs") or []) if isinstance(r, dict)]

    if not events:
        issues.append("events 为空：没有事件就无法复盘因果（不许把'没证据'当成'没问题'）")
    if not runs:
        issues.append("runs 为空：没有子 run 结果就不知道谁失败")

    # 1) 序号单调且不重复（重连/重放会重发，投影必须能靠 seq 去重）
    seqs = [_seq_of(e) for e in events]
    if any(s < 0 for s in seqs):
        issues.append("存在 seq 非法的事件")
    else:
        for prev, cur in zip(seqs, seqs[1:]):
            if cur <= prev:
                issues.append(f"事件序号非单调：{prev} → {cur}")
                break

    # 2) 工具调用与结果必须配对；结果要能指回调用
    pending: dict[str, dict[str, Any]] = {}
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    unpaired_results = 0
    no_causation = 0
    for ev in events:
        etype = str(ev.get("type") or "")
        detail = ev.get("detail") or {}
        if etype == "tool_call":
            tool_calls.append(ev)
            pending[str(detail.get("tool") or "")] = ev
        elif etype == "tool_result":
            tool_results.append(ev)
            tool = str(detail.get("tool") or "")
            if tool not in pending:
                unpaired_results += 1
            else:
                pending.pop(tool)
            if not str(ev.get("causation_id") or ""):
                no_causation += 1
    if pending:
        issues.append(f"{len(pending)} 次工具调用没有结果：{sorted(pending)}")
    if unpaired_results:
        issues.append(f"{unpaired_results} 条工具结果找不到对应调用")
    if no_causation and tool_results:
        issues.append(f"{no_causation}/{len(tool_results)} 条工具结果缺 causation_id（因果链断了）")

    # 3) 子 run 必须有终态
    started = {str((e.get("detail") or {}).get("run_id") or "")
               for e in events if e.get("type") == "agent_run_start"}
    ended = {str((e.get("detail") or {}).get("run_id") or "")
             for e in events if e.get("type") == "agent_run_end"}
    started.discard("")
    dangling = sorted(started - ended)
    if dangling:
        issues.append(f"{len(dangling)} 个子 run 只有 start 没有 end：{dangling[:5]}")

    # 4) 任务必须有终态事件
    if events and not any(e.get("type") == "task_end" for e in events):
        issues.append("缺 task_end：任务没有终态，无法判断它到底成没成")

    return {
        "ok": not issues,
        "issues": issues,
        "stats": {
            "events": len(events),
            "runs": len(runs),
            "tool_calls": len(tool_calls),
            "tool_results": len(tool_results),
            "agent_runs_started": len(started),
            "agent_runs_ended": len(ended),
        },
    }


def replay(bundle: dict[str, Any]) -> dict[str, Any]:
    """回放 = 校验 + 摘要（事件投影走一遍，得出可读结论）。"""
    result = check_bundle(bundle)
    events = [e for e in (bundle.get("events") or []) if isinstance(e, dict)]
    tools = [str((e.get("detail") or {}).get("tool") or "")
             for e in events if e.get("type") == "tool_call"]
    result["summary"] = {
        "task_id": str(bundle.get("task_id") or ""),
        "events": len(events),
        "tools": tools[:20],
        "finish_reason": next((str((e.get("detail") or {}).get("reason") or "")
                               for e in events if e.get("type") == "task_end"), ""),
    }
    return result


def _format(result: dict[str, Any]) -> str:
    head = "✅ bundle 自洽" if result["ok"] else "❌ bundle 有缺口"
    lines = [head, f"  task: {result['summary']['task_id']}",
             f"  事件 {result['stats']['events']} 条 / 子 run {result['stats']['runs']} 个 / "
             f"工具 {result['stats']['tool_calls']} 次",
             f"  收尾原因: {result['summary']['finish_reason'] or '(无)'}"]
    if result["issues"]:
        lines.append("  问题：")
        lines.extend(f"   - {i}" for i in result["issues"])
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clipwright.services.replay",
        description="校验并摘要一份 replay bundle（不重跑模型/工具）",
    )
    parser.add_argument("--bundle", required=True, help="bundle JSON 路径")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        bundle = load_bundle(args.bundle)
    except BundleError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    result = replay(bundle)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
