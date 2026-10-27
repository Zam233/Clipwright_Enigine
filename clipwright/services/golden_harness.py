"""Golden 回归跑批（路线 C / C6，F9 的执行体）。

`golden_runs.py` 定义了"比什么"，这里负责"怎么跑"：

1. 用**录制**跑（`recording.Replayer`）——CI 无网络、无 ffmpeg 也能跑；
2. 每个选题跑一条任务，取产物指标（`metrics_of_timeline`）与基线比；
3. 输出一份可直接贴进 PR 的报告（通过/未通过/差异）。

不做的事：不自动改基线（基线是人确认质量后录的，机器改基线等于自己给自己发证书）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from clipwright.services import golden_runs as gr
from clipwright.services import recording as rec


@dataclass
class TopicRun:
    topic_id: str
    passed: bool = False
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    deltas: dict[str, float] = field(default_factory=dict)
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "passed": self.passed,
            "metrics": self.metrics,
            "failures": self.failures,
            "deltas": self.deltas,
            "error": self.error,
        }


@dataclass
class HarnessReport:
    runs: list[TopicRun] = field(default_factory=list)
    missing_baselines: list[str] = field(default_factory=list)
    stale_baselines: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.runs) and not self.missing_baselines

    def to_public(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total": len(self.runs),
            "failed": [r.topic_id for r in self.runs if not r.passed],
            "missing_baselines": self.missing_baselines,
            "stale_baselines": self.stale_baselines,
            "runs": [r.to_public() for r in self.runs],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Golden 回归报告",
            "",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 选题数：{len(self.runs)}",
        ]
        if self.missing_baselines:
            lines.append(f"- 缺基线：{', '.join(self.missing_baselines)}")
        if self.stale_baselines:
            lines.append(f"- 残留基线（选题已删）：{', '.join(self.stale_baselines)}")
        lines += ["", "| 选题 | 结果 | 关键差异 | 失败原因 |", "|---|---|---|---|"]
        for run in self.runs:
            key_deltas = ", ".join(
                f"{k}{v:+.0%}" for k, v in list(run.deltas.items())[:3]
            ) or "—"
            reason = "; ".join(run.failures) or run.error or "—"
            lines.append(
                f"| {run.topic_id} | {'✅' if run.passed else '❌'} | {key_deltas} | {reason} |"
            )
        return "\n".join(lines) + "\n"


def _metrics_from_recording(replayer: rec.Replayer) -> dict[str, float]:
    """从录制里恢复产物指标。

    录制里 `run_pipeline` 的结果带 `timeline`（C3 能力返回的 output 不含它，
    但录制的是执行器返回的完整 dict，所以兼容两种形状）。
    """
    timeline: Any = None
    for entry in replayer._entries.values():  # noqa: SLF001 — 报告器需要遍历全部录制
        result = entry.result if isinstance(entry.result, dict) else {}
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        candidate = (
            result.get("timeline")
            or output.get("timeline")
            or (result.get("output") if isinstance(output.get("tracks"), list) else None)
        )
        if candidate:
            timeline = candidate
    if not timeline:
        return {}
    return gr.metrics_of_timeline(timeline)


async def run_topic(topic: gr.GoldenTopic, baseline: gr.GoldenBaseline,
                    *, recordings_dir: Path, checkpoint_dir: Path) -> TopicRun:
    from clipwright.schema.task import TaskRequest
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    run = TopicRun(topic_id=topic.id)
    recording_path = recordings_dir / f"{topic.id}.jsonl"
    if baseline.recording:
        recording_path = recordings_dir / Path(baseline.recording).name
    if not recording_path.exists():
        run.error = f"缺录制: {recording_path.name}（先录一次再跑回归）"
        return run

    replayer = rec.Replayer(recording_path)
    # M3：回放注入点是 tool loop 的**驱动器**——工具结果全部来自录制，
    # 缺录制就抛 RecordingMiss（响亮失败，不许顺手真跑）。
    kernel = AgentKernel(KernelDeps(
        driver=rec.make_replay_driver(replayer),
        planner=rec.make_replay_planner(replayer),
        checkpoint_dir=checkpoint_dir,
    ))
    task = kernel.create(TaskRequest(topic=topic.topic, duration_sec=topic.duration_sec,
                                     category_plugin_id=topic.category_plugin_id,
                                     plan_only=False))
    try:
        task = await kernel.run(task)
    except Exception as e:  # noqa: BLE001 — 单条选题失败不阻断其余选题
        run.error = f"{type(e).__name__}: {e}"
        return run

    metrics = _metrics_from_recording(replayer)
    if not metrics:
        run.error = "录制里没有可计算的产物指标（timeline 缺失）"
        return run
    run.metrics = metrics
    result = gr.compare_topic(baseline, metrics)
    run.passed = result.passed
    run.failures = result.failures
    run.deltas = result.deltas
    return run


async def run_all(*, topics_file: str | Path = gr.TOPICS_PATH,
                  baselines_file: str | Path = "tests/golden/baselines.json",
                  recordings_dir: str | Path = "tests/golden/recordings",
                  checkpoint_dir: str | Path = "_cache/golden_tasks") -> HarnessReport:
    topics = gr.load_topics(topics_file)
    baselines = gr.load_baselines(baselines_file)
    report = HarnessReport()
    report.missing_baselines = gr.topics_missing_baseline(topics, baselines)
    report.stale_baselines = gr.stale_baselines(topics, baselines)

    for topic in topics:
        baseline = baselines.get(topic.id)
        if baseline is None:
            report.runs.append(TopicRun(topic_id=topic.id, error="无基线"))
            continue
        report.runs.append(await run_topic(
            topic, baseline,
            recordings_dir=Path(recordings_dir),
            checkpoint_dir=Path(checkpoint_dir),
        ))
    return report


def main(argv: Optional[list[str]] = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    out_path = "docs/golden-report.md"
    if "--out" in args:
        out_path = args[args.index("--out") + 1]
    report = asyncio.run(run_all())
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(report.to_markdown(), encoding="utf-8")
    print(json.dumps(report.to_public(), ensure_ascii=False)[:800])
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
