"""Golden runs（路线 C / C0 交付物，F9 的第二个地基）。

一组**固定的代表性选题** + 一组**可比的指标**，用来回答一个 C 之后必须一直能回答的问题：
"这次改动让成片变差了吗？"

两条设计纪律：

1. **指标比产物更好用**：timeline JSON 直接 diff 会被无关的 id/时间戳噪声淹掉；
   这里先用 `metrics_of_timeline()` 把产物压成 8 个可比数字，再做阈值判断。
2. **阈值单向**：质量类指标（轨道/片段/字幕覆盖）允许 ±10% 抖动；成本/耗时允许
   +30%（PRD 非功能要求）；**失败即红**，不允许"看起来差不多就过"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

#: 选题定义文件（相对仓库根）
TOPICS_PATH = Path("tests/golden/topics.json")

#: 质量类指标的允许抖动（相对基线的比例）
QUALITY_TOLERANCE = 0.10
#: 成本/耗时类指标的允许上浮（PRD：成本增幅 ≤30%）
COST_TOLERANCE = 0.30


class GoldenError(RuntimeError):
    """golden 定义或比对失败。"""


@dataclass(frozen=True)
class GoldenTopic:
    id: str
    topic: str
    persona_id: str
    category_plugin_id: str
    duration_sec: float
    notes: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "persona_id": self.persona_id,
            "category_plugin_id": self.category_plugin_id,
            "duration_sec": self.duration_sec,
            "notes": self.notes,
        }


@dataclass
class GoldenBaseline:
    """一条基线记录：指标 + 录制文件（可离线重演）。"""

    topic_id: str
    metrics: dict[str, float]
    recording: str = ""
    captured_at: str = ""
    commit: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "metrics": self.metrics,
            "recording": self.recording,
            "captured_at": self.captured_at,
            "commit": self.commit,
        }


@dataclass
class ComparisonResult:
    topic_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    deltas: dict[str, float] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "passed": self.passed,
            "failures": self.failures,
            "deltas": self.deltas,
        }


def load_topics(path: str | Path = TOPICS_PATH) -> list[GoldenTopic]:
    p = Path(path)
    if not p.exists():
        raise GoldenError(f"golden 选题定义不存在: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    topics_raw = raw.get("topics") if isinstance(raw, dict) else raw
    if not isinstance(topics_raw, list) or not topics_raw:
        raise GoldenError("golden 选题定义必须是数组或含 topics 数组的对象")
    out: list[GoldenTopic] = []
    seen: set[str] = set()
    for item in topics_raw:
        if not isinstance(item, dict):
            raise GoldenError(f"选题条目必须是对象: {item!r}")
        missing = [k for k in ("id", "topic", "duration_sec") if not item.get(k)]
        if missing:
            raise GoldenError(f"选题 {item.get('id', '?')} 缺少字段: {missing}")
        tid = str(item["id"])
        if tid in seen:
            raise GoldenError(f"选题 id 重复: {tid}")
        seen.add(tid)
        out.append(
            GoldenTopic(
                id=tid,
                topic=str(item["topic"]),
                persona_id=str(item.get("persona_id") or ""),
                category_plugin_id=str(item.get("category_plugin_id") or ""),
                duration_sec=float(item["duration_sec"]),
                notes=str(item.get("notes") or ""),
            )
        )
    return out


def metrics_of_timeline(timeline: dict[str, Any] | Any) -> dict[str, float]:
    """把 timeline（或时间线 dict）压成可比指标。

    只取"用户能感知"的量：时长、轨道数、片段数、字幕条数/覆盖率、音乐轨、转场数。
    噪声源（id、时间戳、绝对路径）一律不进指标。
    """
    if not isinstance(timeline, dict):
        model_dump = getattr(timeline, "model_dump", None)
        timeline = model_dump(mode="json") if callable(model_dump) else {}
    tracks = timeline.get("tracks") or []
    clips = [c for t in tracks for c in (t.get("clips") or []) if isinstance(c, dict)]
    subs = [c for c in clips if str(c.get("kind") or "").lower() in ("subtitle", "text")]
    audios = [c for c in clips if str(c.get("kind") or "").lower() == "audio"]

    def _num(v: Any) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    duration = _num(timeline.get("duration_sec"))
    if duration <= 0:
        duration = max((_num(c.get("start_sec")) + _num(c.get("duration_sec")) for c in clips),
                       default=0.0)
    sub_duration = sum(_num(c.get("duration_sec")) for c in subs)
    return {
        "duration_sec": round(duration, 3),
        "tracks": float(len(tracks)),
        "clips": float(len(clips)),
        "subtitles": float(len(subs)),
        "subtitle_coverage": round(sub_duration / duration, 4) if duration > 0 else 0.0,
        "audio_clips": float(len(audios)),
        "video_clips": float(len([c for c in clips
                                  if str(c.get("kind") or "").lower() in ("video", "image")])),
        "transitions": float(sum(1 for c in clips if c.get("transition"))),
    }


def compare(
    baseline: dict[str, float],
    actual: dict[str, float],
    *,
    quality_tolerance: float = QUALITY_TOLERANCE,
    cost_tolerance: float = COST_TOLERANCE,
) -> ComparisonResult:
    """按阈值比对指标。

    - `cost_usd` / `wall_sec` / `tokens` 走 +30% 单向阈值（只惩罚变贵变慢）；
    - 其余走 ±10% 抖动（只惩罚明显缩水，变多通常无害但仍记录 delta）。
    """
    failures: list[str] = []
    deltas: dict[str, float] = {}
    cost_keys = ("cost_usd", "wall_sec", "tokens")
    for key, base in baseline.items():
        if base <= 0:
            continue
        value = float(actual.get(key, 0.0))
        delta = (value - base) / base
        deltas[key] = round(delta, 4)
        if key in cost_keys:
            if delta > cost_tolerance:
                failures.append(
                    f"{key} 超出上限：{value:.3f} vs 基线 {base:.3f}（+{delta:.0%} > "
                    f"{cost_tolerance:.0%}）"
                )
        else:
            if delta < -quality_tolerance:
                failures.append(
                    f"{key} 明显下降：{value:.3f} vs 基线 {base:.3f}（{delta:.0%} < "
                    f"-{quality_tolerance:.0%}）"
                )
    return ComparisonResult(topic_id="", passed=not failures, failures=failures, deltas=deltas)


def compare_topic(baseline: GoldenBaseline, metrics: dict[str, float]) -> ComparisonResult:
    result = compare(baseline.metrics, metrics)
    result.topic_id = baseline.topic_id
    return result


def assert_golden_pass(baseline: GoldenBaseline, metrics: dict[str, float]) -> None:
    result = compare_topic(baseline, metrics)
    if not result.passed:
        raise GoldenError(
            f"选题 {baseline.topic_id} 未过 golden 门槛：\n- " + "\n- ".join(result.failures)
        )


def load_baselines(path: str | Path) -> dict[str, GoldenBaseline]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw.get("baselines") if isinstance(raw, dict) else raw
    out: dict[str, GoldenBaseline] = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("topic_id"):
            raise GoldenError(f"基线条目缺 topic_id: {item!r}")
        out[str(item["topic_id"])] = GoldenBaseline(
            topic_id=str(item["topic_id"]),
            metrics={k: float(v) for k, v in (item.get("metrics") or {}).items()},
            recording=str(item.get("recording") or ""),
            captured_at=str(item.get("captured_at") or ""),
            commit=str(item.get("commit") or ""),
        )
    return out


def topics_missing_baseline(topics: Optional[list[GoldenTopic]] = None,
                            baselines: Optional[dict[str, GoldenBaseline]] = None
                            ) -> list[str]:
    """哪些选题还没有基线（C0 结束时应为空；C0 期间允许非空但要可见）。"""
    topics = topics if topics is not None else load_topics()
    baselines = baselines if baselines is not None else {}
    return [t.id for t in topics if t.id not in baselines]


def stale_baselines(topics: Optional[list[GoldenTopic]] = None,
                    baselines: Optional[dict[str, GoldenBaseline]] = None) -> list[str]:
    """基线里有、选题里已不存在的 id（删选题后忘删基线会让"通过"变假象）。"""
    topics = topics if topics is not None else load_topics()
    baselines = baselines if baselines is not None else {}
    known = {t.id for t in topics}
    return sorted(tid for tid in baselines if tid not in known)
