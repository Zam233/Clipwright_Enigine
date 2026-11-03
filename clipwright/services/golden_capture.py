"""Golden 录制与基线捕获（路线 C / C6 的前置工具）。

存在的理由：`golden_harness` 只能**回放**，而回放需要"别人先录过一次"。
这个模块就是那个"第一次"：真跑一条选题，把调用录下来，并把产物指标写成基线。

两条纪律：

1. **基线不自动覆盖**：已有基线时默认拒绝覆盖，必须显式 `overwrite=True`
   （机器自动改基线 = 自己给自己发证书，C6 的比对就失去意义）。
2. **录制失败不留半成品**：先写临时文件，成功后再改名；否则下次回放会读到
   截断的 JSONL，报一堆看不懂的错。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from clipwright.services import golden_runs as gr
from clipwright.services import recording as rec


class CaptureError(RuntimeError):
    """录制/基线写入失败。"""


@dataclass
class CaptureResult:
    topic_id: str
    recording_path: str
    metrics: dict[str, float]
    calls: int
    baseline_written: bool

    def to_public(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "recording_path": self.recording_path,
            "metrics": self.metrics,
            "calls": self.calls,
            "baseline_written": self.baseline_written,
        }


def _timeline_of(task: Any, last_result: Optional[dict[str, Any]]) -> dict[str, Any]:
    """优先用管线真实产物；没有就用任务摘要算（指标会少但不会假）。"""
    if isinstance(last_result, dict):
        timeline = last_result.get("timeline") or (
            last_result.get("shared_data") or {}
        ).get("timeline")
        if isinstance(timeline, dict):
            return timeline
    return {}


async def capture_topic(
    topic: gr.GoldenTopic,
    *,
    recordings_dir: str | Path = "tests/golden/recordings",
    baselines_file: str | Path = "tests/golden/baselines.json",
    checkpoint_dir: str | Path = "_cache/golden_capture",
    overwrite: bool = False,
    driver: Any = None,
    planner: Any = None,
) -> CaptureResult:
    """真跑一条选题并落录制 + 基线。`driver` 可注入（测试用；M3 后 Kernel 唯一注入点）。"""
    from clipwright.schema.task import TaskRequest
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    recordings = Path(recordings_dir)
    recordings.mkdir(parents=True, exist_ok=True)
    target = recordings / f"{topic.id}.jsonl"
    if target.exists() and not overwrite:
        raise CaptureError(
            f"录制已存在: {target}（基线是人工确认过质量的数据，默认不覆盖；"
            "确需重录请显式 overwrite=True）"
        )

    baselines = gr.load_baselines(baselines_file)
    if topic.id in baselines and not overwrite:
        raise CaptureError(
            f"基线已存在: {topic.id}（覆盖等于自己给自己发证书；确需重录请显式 overwrite=True）"
        )

    fd, tmp_name = tempfile.mkstemp(suffix=".jsonl", dir=str(recordings))
    os.close(fd)
    tmp_path = Path(tmp_name)

    # M3：Kernel 没有 planner/executor 了——注入点是 tool loop 的**驱动器**。
    kernel = AgentKernel(KernelDeps(
        driver=driver,
        planner=planner,          # 只用于产出一份"初始假设"，不驱动执行
        checkpoint_dir=Path(checkpoint_dir),
    ))
    task = kernel.create(TaskRequest(
        topic=topic.topic,
        duration_sec=topic.duration_sec,
        category_plugin_id=topic.category_plugin_id,
        plan_only=False,
    ))
    with rec.recording_scope(rec.Recorder(tmp_path)):
        task = await kernel.run(task)

    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise CaptureError(f"录制为空（任务状态={task.status}）：拒绝写半成品录制")

    from clipwright.services import golden_harness as gh
    from clipwright.services.task_results import last_result

    # 指标优先从**录制**里算：那是回放时的唯一事实来源，两边算法必须同源
    # 注意用 tmp_path（录制还没改名到 target，读 target 会得到空结果）
    metrics = gh._metrics_from_recording(rec.Replayer(tmp_path))  # noqa: SLF001
    if not metrics:
        timeline = _timeline_of(task, last_result(task.id))
        metrics = gr.metrics_of_timeline(timeline) if timeline else {}
    if not metrics and task.status != "done":
        tmp_path.unlink(missing_ok=True)
        raise CaptureError(
            f"任务未成功（{task.status}）且无产物指标：不写基线（否则会把失败录成基线）"
        )
    if not metrics:
        metrics = {"duration_sec": float(task.request.duration_sec or 0.0)}

    tmp_path.replace(target)
    written = _write_baseline(
        baselines_file, topic, metrics, recording_name=target.name, overwrite=overwrite,
    )
    return CaptureResult(
        topic_id=topic.id,
        recording_path=str(target),
        metrics=metrics,
        calls=len(rec.Replayer(target)),
        baseline_written=written,
    )


def _write_baseline(
    baselines_file: str | Path, topic: gr.GoldenTopic, metrics: dict[str, float],
    *, recording_name: str, overwrite: bool,
) -> bool:
    path = Path(baselines_file)
    existing = gr.load_baselines(path) if path.exists() else {}
    if topic.id in existing and not overwrite:
        return False
    payload = {
        "baselines": [
            {**entry.to_public(), "metrics": entry.metrics}
            for tid, entry in existing.items() if tid != topic.id
        ]
    }
    payload["baselines"].append({
        "topic_id": topic.id,
        "metrics": metrics,
        "recording": recording_name,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


async def capture_all(
    topic_ids: Optional[list[str]] = None,
    *,
    topics_file: str | Path = gr.TOPICS_PATH,
    recordings_dir: str | Path = "tests/golden/recordings",
    baselines_file: str | Path = "tests/golden/baselines.json",
    overwrite: bool = False,
) -> list[CaptureResult]:
    topics = gr.load_topics(topics_file)
    wanted = set(topic_ids or [t.id for t in topics])
    out: list[CaptureResult] = []
    for topic in topics:
        if topic.id not in wanted:
            continue
        out.append(await capture_topic(
            topic, recordings_dir=recordings_dir, baselines_file=baselines_file,
            overwrite=overwrite,
        ))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    ids = [a for a in args if not a.startswith("--")]
    overwrite = "--overwrite" in args
    results = asyncio.run(capture_all(ids or None, overwrite=overwrite))
    for r in results:
        print(json.dumps(r.to_public(), ensure_ascii=False))
    return 0 if results else 1


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
