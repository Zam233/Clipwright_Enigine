"""创作提示词 A/B 的指标提取（V45）。

把"提示词到底改了什么"从感觉变成数字：

- **关键词命中**：提示词里的词（版画/三墨/套印/硬切/花哨/印刷）在时间线元数据里
  出现多少次。控制组应当接近 0，实验组应当明显 > 0——这是"提示词真的改写了执行
  内容"最直接的可数证据；
- **镜头节奏**：视频片段数、平均镜头时长、最短/最长。提示词说"硬切为主"，节奏
  应当收紧（平均镜头变短、片段变多）；
- **元数据覆盖**：多少视频片段带了 `visual_description`（结构阶段有没有把画面要求
  写细）。

纯函数，输入是 trace 里的 `timeline_snapshot` detail，不碰网络/数据库。
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from typing import Any

#: 探针提示词里的可数关键词
PROMPT_KEYWORDS: tuple[str, ...] = ("版画", "三墨", "套印", "硬切", "花哨", "印刷")


def timeline_metrics(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """从时间线快照抽可比指标。"""
    snapshot = snapshot or {}
    clips: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for track in snapshot.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        tracks.append({
            "name": track.get("name"),
            "kind": track.get("kind"),
            "clips": len(track.get("clips") or []),
        })
        if track.get("kind") == "video":
            clips.extend(c for c in (track.get("clips") or []) if isinstance(c, dict))

    durations = [round(float(c.get("duration_sec") or 0), 2) for c in clips]
    transitions = Counter(str(c.get("transition_in") or "none") for c in clips)
    snapshot_text = json.dumps(snapshot, ensure_ascii=False)
    keyword_hits = {word: snapshot_text.count(word) for word in PROMPT_KEYWORDS}
    described = sum(
        1 for c in clips
        if isinstance((c.get("metadata") or {}).get("visual_description"), dict)
    )
    return {
        "duration_sec": snapshot.get("duration_sec"),
        "video_clips": len(clips),
        "tracks": tracks,
        "clip_durations_sec": durations,
        "mean_clip_sec": round(statistics.fmean(durations), 2) if durations else None,
        "min_clip_sec": min(durations) if durations else None,
        "max_clip_sec": max(durations) if durations else None,
        "transitions": dict(transitions),
        "hard_cut_share": (
            round(transitions.get("hard_cut", 0) / len(clips), 3) if clips else None
        ),
        "keyword_hits": keyword_hits,
        "keyword_total": sum(keyword_hits.values()),
        "clips_with_visual_description": described,
    }


def diff_metrics(control: dict[str, Any], prompted: dict[str, Any]) -> dict[str, Any]:
    """对照组 / 实验组的差值（只比数值型指标）。"""
    out: dict[str, Any] = {}
    for key in (
        "video_clips",
        "mean_clip_sec",
        "hard_cut_share",
        "duration_sec",
        "keyword_total",
        "clips_with_visual_description",
    ):
        control_value = control.get(key)
        prompted_value = prompted.get(key)
        if isinstance(control_value, (int, float)) and isinstance(prompted_value, (int, float)):
            out[key] = {
                "control": control_value,
                "prompted": prompted_value,
                "delta": round(prompted_value - control_value, 3),
            }
    return out


__all__ = ["PROMPT_KEYWORDS", "diff_metrics", "timeline_metrics"]
