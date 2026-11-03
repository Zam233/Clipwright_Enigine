"""拍点与 cues 服务（PRD C7 / R-13）。

三件事：
1. **拍网格**：``bpm`` + ``beat_zero_sec`` → 一拍 = 60/bpm 秒；支持拍/半拍/小节吸附；
2. **cues 表**：所有时间集中到一份 ``cues.json``，每项标 ``measured``（实测起音）
   还是 ``init``（初值，待耳朵确认）——参考样本把这条当成硬要求；
3. **对齐校验**：切点是否落在网格上、误差多少帧（喂给验收 V24）。

不调音频、不调 LLM；音频起音由调用方传入（``content_audio`` / whisper 均可）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from clipwright.schema.lyrics import LyricsDoc

#: 吸附单位 → 拍数。
SNAP_UNITS: dict[str, float] = {"beat": 1.0, "half": 0.5, "bar": 4.0}


class BeatGrid(BaseModel):
    """拍网格。``bpm`` 为 0 时视为"无网格"（吸附函数原样返回）。"""

    bpm: float = 0.0
    beat_zero_sec: float = 0.0

    @property
    def beat_sec(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    @property
    def bar_sec(self) -> float:
        return self.beat_sec * 4.0

    def beat_index(self, t: float) -> float:
        if self.beat_sec <= 0:
            return 0.0
        return (float(t) - self.beat_zero_sec) / self.beat_sec

    def time_of_beat(self, index: float) -> float:
        return self.beat_zero_sec + float(index) * self.beat_sec

    def snap(self, t: float, unit: str = "beat") -> float:
        """把时间吸附到最近的网格点（``unit`` ∈ beat/half/bar）。"""
        if unit not in SNAP_UNITS:
            raise ValueError(f"未知吸附单位 {unit!r}；可用 {sorted(SNAP_UNITS)}")
        if self.beat_sec <= 0:
            return float(t)
        step = SNAP_UNITS[unit]
        index = self.beat_index(t) / step
        return round(self.time_of_beat(round(index) * step), 6)

    def error_frames(self, t: float, fps: float, unit: str = "beat") -> float:
        """离最近网格点差几帧（验收 V24 用）。"""
        if fps <= 0:
            raise ValueError("fps 必须为正")
        snapped = self.snap(t, unit)
        return abs(float(t) - snapped) * float(fps)

    def is_on_grid(self, t: float, fps: float = 60.0, unit: str = "beat", tolerance_frames: float = 1.0) -> bool:
        return self.error_frames(t, fps, unit) <= tolerance_frames


class Cue(BaseModel):
    """cues.json 的一项：名称、时间、来源（measured/init）、吸附单位。"""

    name: str
    t: float
    source: str = "init"
    snapped: str = "beat"
    note: str = ""


class CuesDoc(BaseModel):
    grid: BeatGrid = Field(default_factory=BeatGrid)
    items: list[Cue] = Field(default_factory=list)

    def by_name(self, name: str) -> Optional[Cue]:
        for item in self.items:
            if item.name == name:
                return item
        return None

    def init_count(self) -> int:
        return sum(1 for item in self.items if item.source != "measured")

    def pending_confirmation(self) -> list[dict[str, Any]]:
        """待确认清单：`init`（初值未实测）的 cue，逐条列出供耳朵复核。

        V25 给的是"逐词误差 ≤42ms **或** 标 init 并列入待确认清单"。没有实测起音
        数据时走的就是这条：**不是假装准，而是把没验证过的项显式列出来**。
        """
        return [
            {
                "name": item.name,
                "time_sec": round(item.t, 4),
                "source": item.source,
                "snapped": item.snapped,
                "note": item.note,
                "reason": "无实测起音数据，时间为按拍吸附的初值",
            }
            for item in self.items
            if item.source != "measured"
        ]


def build_grid(bpm: float, beat_zero_sec: float = 0.0) -> BeatGrid:
    if bpm < 0:
        raise ValueError("bpm 不能为负")
    return BeatGrid(bpm=float(bpm), beat_zero_sec=float(beat_zero_sec))


def pick_nearest_onset(t: float, onsets: Iterable[float], window: float = 0.25) -> Optional[float]:
    """取最近的实测起音（超出窗口返回 None，保留初值）。"""
    best: Optional[float] = None
    best_dist = float("inf")
    for onset in onsets or []:
        dist = abs(float(onset) - float(t))
        if dist < best_dist and dist <= window:
            best, best_dist = float(onset), dist
    return best


def make_cue(
    name: str,
    t: float,
    *,
    grid: Optional[BeatGrid] = None,
    unit: str = "beat",
    onsets: Optional[Iterable[float]] = None,
    onset_window: float = 0.25,
    note: str = "",
) -> Cue:
    """构造一项 cue：优先用实测起音，其次吸附到网格，两者都没有则保留初值。"""
    measured = pick_nearest_onset(t, onsets or [], onset_window)
    if measured is not None:
        final = measured
        source = "measured"
    elif grid is not None and grid.beat_sec > 0:
        final = grid.snap(t, unit)
        source = "init"
    else:
        final = float(t)
        source = "init"
    return Cue(name=name, t=round(float(final), 6), source=source, snapped=unit, note=note)


def cues_from_lyrics(
    lyrics: LyricsDoc,
    *,
    grid: Optional[BeatGrid] = None,
    unit: str = "half",
    onsets: Optional[Iterable[float]] = None,
    onset_window: float = 0.25,
) -> list[Cue]:
    """由歌词行生成 cues（每行开唱 + 每个关键词）。"""
    items: list[Cue] = []
    for line in lyrics.lines:
        items.append(
            make_cue(
                f"line_{line.i}",
                line.t0,
                grid=grid,
                unit=unit,
                onsets=onsets,
                onset_window=onset_window,
                note=line.text_ref,
            )
        )
        for word in line.keywords():
            items.append(
                make_cue(
                    f"key_{line.i}_{word.w}",
                    word.t0,
                    grid=grid,
                    unit="beat",
                    onsets=onsets,
                    onset_window=onset_window,
                )
            )
        for index, moment in enumerate(line.syllables):
            items.append(
                make_cue(
                    f"syl_{line.i}_{index}",
                    moment,
                    grid=grid,
                    unit="beat",
                    onsets=onsets,
                    onset_window=onset_window,
                )
            )
    return items


def build_cues(
    lyrics: LyricsDoc,
    *,
    bpm: float = 0.0,
    beat_zero_sec: float = 0.0,
    unit: str = "half",
    onsets: Optional[Iterable[float]] = None,
    extra_names: Optional[Iterable[tuple[str, float, str]]] = None,
) -> CuesDoc:
    """生成完整 cues 文档；``extra_names`` 为 ``(name, t, unit)`` 的补充切点。"""
    grid = build_grid(bpm, beat_zero_sec)
    items = cues_from_lyrics(lyrics, grid=grid, unit=unit, onsets=onsets)
    for name, t, extra_unit in extra_names or []:
        items.append(make_cue(name, t, grid=grid, unit=extra_unit, onsets=onsets))
    return CuesDoc(grid=grid, items=items)


def grid_hit_rate(
    times: Iterable[float],
    grid: BeatGrid,
    *,
    fps: float = 60.0,
    unit: str = "beat",
    tolerance_frames: float = 1.0,
) -> dict[str, Any]:
    """切点命中率（验收 V24）：返回命中数与最差误差。"""
    times = [float(t) for t in times]
    if not times:
        return {"total": 0, "hits": 0, "rate": 1.0, "worst_frames": 0.0}
    errors = [grid.error_frames(t, fps, unit) for t in times]
    hits = sum(1 for error in errors if error <= tolerance_frames)
    return {
        "total": len(times),
        "hits": hits,
        "rate": hits / len(times),
        "worst_frames": max(errors),
    }


def save_cues(path: str | Path, doc: CuesDoc) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "grid": {"bpm": doc.grid.bpm, "beat_zero_sec": doc.grid.beat_zero_sec,
                 "beat_sec": round(doc.grid.beat_sec, 6)},
        "items": [item.model_dump() for item in doc.items],
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return file_path


def load_cues(path: str | Path) -> CuesDoc:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    grid_raw = payload.get("grid") or {}
    grid = BeatGrid(bpm=float(grid_raw.get("bpm") or 0), beat_zero_sec=float(grid_raw.get("beat_zero_sec") or 0))
    items = [Cue(**item) for item in payload.get("items") or []]
    return CuesDoc(grid=grid, items=items)


def beat_positions(grid: BeatGrid, duration_sec: float, unit: str = "beat") -> list[float]:
    """生成 [0, duration] 内的网格点（供场景脚本画拍线/调试）。"""
    if grid.beat_sec <= 0 or duration_sec <= 0:
        return []
    step = SNAP_UNITS.get(unit, 1.0) * grid.beat_sec
    count = int(math.floor((duration_sec - grid.beat_zero_sec) / step))
    return [round(grid.beat_zero_sec + i * step, 6) for i in range(count + 1)]


__all__ = [
    "SNAP_UNITS",
    "BeatGrid",
    "Cue",
    "CuesDoc",
    "beat_positions",
    "build_cues",
    "build_grid",
    "cues_from_lyrics",
    "grid_hit_rate",
    "load_cues",
    "make_cue",
    "pick_nearest_onset",
    "save_cues",
]
