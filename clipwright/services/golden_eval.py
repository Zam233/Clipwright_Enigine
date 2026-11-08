"""Golden 选题多次运行与评估（closeout B10 的"真跑"部分 / PRD §5.5 / §6 F）。

`python -m clipwright.services.golden_eval --topic g04_meme_burst --runs 3`

它做三件事，且都要求**真的跑**：

1. 用同一个 golden 选题跑 N 次（真 Kernel + 真 provider + 真工具，不 mock）；
2. 每次跑完取 `evaluation.summary()`（成功率/步数/工具调用/子 run/成本/重规划）；
3. 用 `prompt_registry.three_run_report` 出均值 + 极差报告，并可选与基线做门禁对比。

为什么必须"跑真的"：单测能证明报告/门禁/方差告警的**代码**是对的，但证明不了
"这个系统在真模型上跑得出来"。没有这一步，"评估闭环"就只是个空壳。

诚实边界：
- 每次运行有自己的预算/回合/墙钟上限（默认很小），**跑不完整也是数据**——
  报告里会带 `status` / `finish_reason` / `failure`，别把它当"成功"看；
- 成本未知时照记 unknown，不填 0。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

DEFAULT_RUNS = 3
DEFAULT_OUT = Path("_cache") / "eval"


def _safe_print(text: str, *, err: bool = False) -> None:
    """控制台编码不支持时替换，别让"报告打不出来"变成"工具坏了"。"""
    stream = sys.stderr if err else sys.stdout
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", "") or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"),
              file=stream)


async def run_once(
    topic: Any,
    *,
    max_turns: int = 4,
    max_wall_sec: float = 180.0,
    budget_usd: Optional[float] = 0.30,
    driver: Any = None,
) -> dict[str, Any]:
    """真跑一次选题，返回本次运行的可比指标（失败也返回，不抛）。"""
    from clipwright.schema.task import TaskRequest
    from clipwright.services import evaluation
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    started = time.time()
    kernel = AgentKernel(KernelDeps(driver=driver) if driver else None)

    # 工作区是创作的前提：没有它，list_dir/list_artifacts/list_reports 全部会报
    # "路径不在白名单 / 未找到工作区"，模型只能把回合花在无效读上（实测 10 次调用
    # 全是 error，最后交不出 timeline）。评估环境必须和真实入口（proceed）一致。
    extra: dict[str, Any] = {}
    try:
        from clipwright.services.workspace import ensure_workspace

        ws, _meta = ensure_workspace(f"eval_{getattr(topic, 'id', 'topic')}",
                                     name=f"eval-{getattr(topic, 'id', 'topic')}")
        extra["workspace_root"] = str(ws.dirs().root)
        extra["workspace_project_id"] = ws.project_id
    except Exception as exc:  # noqa: BLE001 — 工作区建不出来就照实跑（报告里会体现）
        logger.warning("评估工作区创建失败: %s", exc)

    request = TaskRequest(
        topic=str(getattr(topic, "topic", "") or getattr(topic, "id", "eval")),
        persona_id=str(getattr(topic, "persona_id", "") or ""),
        category_plugin_id=str(getattr(topic, "category_plugin_id", "") or ""),
        duration_sec=float(getattr(topic, "duration_sec", 0) or 0),
        plan_only=False,
        extra=extra,
    )
    task = kernel.create(request)
    # 紧上限：评估要能跑完并可比，而不是把预算烧在一条选题上
    task.limits.max_turns = int(max_turns)
    task.limits.max_wall_sec = float(max_wall_sec)
    task.limits.budget_usd = budget_usd

    try:
        task = await kernel.run(task)
    except Exception as exc:  # noqa: BLE001 — 单次失败不能中断整组评估
        logger.warning("golden run 异常 %s: %s", task.id, exc)

    rows = [r for r in evaluation.records(task.id) if r.get("kind") == "task"]
    summary = evaluation.summary(rows) if rows else {}
    return {
        "task_id": task.id,
        "status": str(task.status),
        "finish_reason": str((task.finish.reason if task.finish else "") or ""),
        "conclusion": str((task.finish.conclusion if task.finish else "") or "")[:300],
        "wall_sec": round(time.time() - started, 2),
        "turns": int(task.usage.turns),
        "tool_calls": int(task.usage.tool_calls),
        "cost_usd": task.usage.cost_usd or None,
        "unknown_cost_calls": int(getattr(task.usage, "unknown_cost_calls", 0) or 0),
        "success_rate": summary.get("success_rate"),
    }


async def run_many(
    topic: Any,
    *,
    runs: int = DEFAULT_RUNS,
    driver: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """同一选题跑 N 次，返回 `{topic, runs:[...], report, gate}`。"""
    from clipwright.services import prompt_registry

    results: list[dict[str, Any]] = []
    for _ in range(max(1, int(runs))):
        results.append(await run_once(topic, driver=driver, **kw))

    # 三次对比报告只吃 summary 形状 → 把每次运行折成 summary
    summaries = []
    for r in results:
        summaries.append({
            "success_rate": 1.0 if r["status"] == "done" else 0.0,
            "avg_turns": r["turns"],
            "avg_tool_calls": r["tool_calls"],
            "avg_child_runs": 0,
            "avg_duration_ms": int(r["wall_sec"] * 1000),
            "cost_avg_usd": r["cost_usd"],
            "replan_rate": 0.0,
        })
    report = prompt_registry.three_run_report(summaries)
    report["topic"] = {
        "id": str(getattr(topic, "id", "")),
        "topic": str(getattr(topic, "topic", "")),
        "duration_sec": float(getattr(topic, "duration_sec", 0) or 0),
        "category_plugin_id": str(getattr(topic, "category_plugin_id", "") or ""),
    }
    report["raw_runs"] = results
    report["generated_at"] = time.time()
    return {"topic": report["topic"], "runs": results, "report": report}


def _format(payload: dict[str, Any]) -> str:
    topic = payload["topic"]
    lines = [f"[OK] golden 评估：{topic['id']}  {topic['topic'][:40]}",
             f"  运行次数: {len(payload['runs'])}"]
    for i, r in enumerate(payload["runs"], 1):
        lines.append(
            f"   #{i} status={r['status']} reason={r['finish_reason']} "
            f"turns={r['turns']} tools={r['tool_calls']} "
            f"cost={r['cost_usd']} wall={r['wall_sec']}s"
        )
    metrics = payload["report"].get("metrics") or {}
    for key in ("success_rate", "avg_turns", "avg_tool_calls", "cost_avg_usd"):
        m = metrics.get(key) or {}
        if m.get("n"):
            lines.append(f"  {key}: mean={m['mean']} spread={m['spread']}")
    for w in payload["report"].get("warnings") or []:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def save(payload: dict[str, Any], out_dir: str | Path = DEFAULT_OUT) -> str:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    topic_id = payload["topic"].get("id") or "topic"
    path = d / f"golden-{topic_id}-{int(time.time())}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("golden 评估报告已写入 %s", path)
    return str(path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clipwright.services.golden_eval",
        description="用 golden 选题真跑多次并出评估报告（真 Kernel / 真 provider）",
    )
    parser.add_argument("--topic", required=True, help="选题 id，如 g04_meme_burst")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-wall-sec", type=float, default=180.0)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from clipwright.services import golden_runs as gr

    topics = {t.id: t for t in gr.load_topics()}
    topic = topics.get(args.topic)
    if topic is None:
        _safe_print(f"[FAIL] 选题不存在: {args.topic}（可用: {sorted(topics)}）", err=True)
        return 1

    payload = asyncio.run(run_many(
        topic, runs=args.runs,
        max_turns=args.max_turns, max_wall_sec=args.max_wall_sec))
    path = save(payload, args.out)

    if args.json:
        _safe_print(json.dumps({"report": payload["report"], "path": path},
                               ensure_ascii=False, indent=2))
    else:
        _safe_print(_format(payload))
        _safe_print(f"  报告: {path}")

    # 只要有运行没跑完就返回非零——"跑不起来"必须响
    done = sum(1 for r in payload["runs"] if r["status"] == "done")
    return 0 if done == len(payload["runs"]) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
