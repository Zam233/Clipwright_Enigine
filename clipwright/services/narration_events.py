"""NEL（Narration Event Line）— 配音事件时间线提取与对齐工具。

输入：dub_script 的分段结果（每段含 text / start_sec / end_sec / char_timings）。
输出：事件列表 ``[{t, type, payload, text}]``，供 AnimationScheduler / BeatGrid
把动画入场、关键帧与「旁白说到哪里」对齐（Phase 2.3，报告 2.0 目标架构）。

事件类型：
- number   数字（增长率/金额/统计值 —— 信息揭示高价值点）
- emphasis 强调（「」【】引号、感叹号、程度副词）
- question 设问（？/ 是否 / 为什么）
- turn     转折（但是/然而/不过）
- enum     枚举（第一/其次/以及、列举）

规则提取为确定性核心（可单测），LLM 富化可选（失败回退规则）。
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(
    r"-?\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|元|美元|人|次|个|件|秒|分钟|小时|天|年|月|周|GB|TB|MB|KB)?"
)
_EMPHASIS_RE = re.compile(r"[！!]|「[^」]{2,}」|【[^】]{2,}】|“(?:[^”]{2,})”|(?:非常|格外|极其|尤其|大幅|显著|暴涨|暴跌|惊人|关键|核心|最重要)")
_TURN_RE = re.compile(r"(?:但是|然而|不过|可是|却|反而|偏偏|没想到|结果)")
_QUESTION_RE = re.compile(r"[？?]|(?:是否|能不能|会不会|为什么|如何|怎样)")
_ENUM_RE = re.compile(r"(?:第一|第二|第三|首先|其次|接着|最后|一方面|另一方面|一是|二是|三是|[、，]\s*(?:比如|例如))")

_EVENT_TYPES = ("number", "emphasis", "turn", "question", "enum")
# 优先级（pick_nel_event 按此偏好取事件）
_TYPE_PRIORITY = {"number": 5, "emphasis": 4, "turn": 3, "question": 2, "enum": 1}


def _char_time(seg: dict[str, Any], char_idx: int) -> float:
    """段内字符偏移 → 绝对时间（用实测 char_timings；缺失时按字数比例近似）。"""
    text = str(seg.get("text", ""))
    timings = seg.get("char_timings")
    if isinstance(timings, list) and timings:
        idx = max(0, min(char_idx, len(timings) - 1))
        return float(timings[idx])
    start = float(seg.get("start_sec", 0) or 0)
    end = float(seg.get("end_sec", start) or start)
    n = max(1, len(text))
    return start + (end - start) * min(char_idx, n - 1) / n


def _match_cues(seg: dict[str, Any], pattern: re.Pattern, ev_type: str) -> list[dict]:
    text = str(seg.get("text", ""))
    out: list[dict] = []
    for m in pattern.finditer(text):
        payload = m.group(0).strip()
        if not payload:
            continue
        out.append({
            "t": round(_char_time(seg, m.start()), 3),
            "type": ev_type,
            "payload": payload[:80],
            "text": text[:120],
        })
    return out


def extract_nel(segments: list[dict[str, Any]], max_per_type: int = 8) -> list[dict[str, Any]]:
    """规则提取：从配音分段生成 NEL 事件列表（按时间排序，每类型限流）。"""
    events: list[dict] = []
    for seg in segments or []:
        if not (seg.get("text") or "").strip():
            continue
        if not seg.get("start_sec") and seg.get("start_sec") != 0:
            continue  # 无实测时间的段（失败/占位）不参与
        events += _match_cues(seg, _NUMBER_RE, "number")
        events += _match_cues(seg, _EMPHASIS_RE, "emphasis")
        events += _match_cues(seg, _TURN_RE, "turn")
        events += _match_cues(seg, _QUESTION_RE, "question")
        events += _match_cues(seg, _ENUM_RE, "enum")

    # 去重（同一位置同一类型只留一条）+ 每类型限流 + 时间排序
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for ev in events:
        key = (ev["type"], str(ev["t"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)
    counts: dict[str, int] = {}
    capped: list[dict] = []
    for ev in sorted(deduped, key=lambda e: e["t"]):
        t = ev["type"]
        if counts.get(t, 0) >= max_per_type:
            continue
        counts[t] = counts.get(t, 0) + 1
        capped.append(ev)
    return capped


def pick_nel_event(
    nel: list[dict[str, Any]],
    start: float,
    end: float,
    prefer: tuple[str, ...] = ("number", "emphasis"),
    max_shift: float = 1.2,
) -> dict[str, Any] | None:
    """在 [start, end] 窗口内挑最佳 NEL 事件（供动画对齐）。

    - 只挑窗口内（含边界容差）的事件；
    - 优先 prefer 类型（默认数字/强调），再按优先级排序；
    - 事件与窗口起点的偏差超过 max_shift 时不强行对齐（避免动画整体漂移）。
    """
    lo, hi = start - 0.05, end + 0.05
    cands = [e for e in nel if lo <= float(e["t"]) <= hi]
    if not cands:
        return None

    def _key(e: dict) -> tuple[int, float]:
        p = 0
        for i, t in enumerate(prefer):
            if e["type"] == t:
                p = len(prefer) - i
                break
        return (p, _TYPE_PRIORITY.get(e["type"], 0), abs(float(e["t"]) - start))

    best = max(cands, key=_key)
    if abs(float(best["t"]) - start) > max_shift:
        return None
    return best


def snap_to_beat(t: float, bpm: float | None, max_shift: float = 0.25) -> float:
    """BPM 落拍（Phase 2.5）：把时刻吸附到最近的节拍点；偏差超限则不吸。

    拍间隔 = 60/bpm。仅当 |吸附后-原时刻| <= max_shift 时吸附。
    """
    if not bpm or bpm <= 0:
        return t
    beat = 60.0 / float(bpm)
    idx = round(t / beat)
    snapped = idx * beat
    if abs(snapped - t) <= max_shift:
        return round(snapped, 3)
    return t


def attach_nel_to_timeline(timeline, segments: list[dict], bpm: float | None = None) -> dict:
    """把 NEL + BPM 挂到旁白轨 metadata（AnimationAgent 读取）。

    返回 {events, bpm}；无旁白轨时 no-op。
    """
    events = extract_nel(segments)
    from clipwright.config import logger
    from clipwright.schema.timeline import ClipKind

    for track in getattr(timeline, "tracks", []) or []:
        if track.kind == ClipKind.AUDIO and any(
            (getattr(c, "metadata", {}) or {}).get("narration") for c in track.clips
        ):
            track.metadata["nel"] = events
            if bpm:
                track.metadata["bpm"] = bpm
            logger.info("NEL: 提取 %d 个配音事件挂到 %s (bpm=%s)", len(events), track.id, bpm)
            return {"events": events, "bpm": bpm, "track_id": track.id}
    return {"events": events, "bpm": bpm, "track_id": ""}


def timeline_nel_bpm(timeline) -> tuple[list[dict], float | None]:
    """从时间轴读取 NEL 事件列表与 BPM（AudioAgent 后置接线后可用）。"""
    nel: list[dict] = []
    bpm: float | None = None
    for t in getattr(timeline, "tracks", []) or []:
        md = getattr(t, "metadata", {}) or {}
        if isinstance(md.get("nel"), list) and md["nel"]:
            nel = md["nel"]
        if md.get("bpm"):
            bpm = float(md["bpm"])
        for c in getattr(t, "clips", []) or []:
            cm = getattr(c, "metadata", {}) or {}
            if cm.get("bpm"):
                bpm = float(cm["bpm"])
    return nel, bpm


def align_animations_to_nel(
    timeline,
    prefer: tuple[str, ...] = ("number", "emphasis"),
    max_shift: float = 1.2,
) -> dict:
    """Phase 2.4/2.5：后置对齐 pass — 把已生成的 MG 动画 clip 吸附到 NEL 事件/节拍。

    调用时机：AudioAgent 挂完 NEL 之后（动画 Agent 在其前运行，生成时 NEL 尚不存在）。
    - 窗口内有 NEL 事件（默认优先数字/强调）→ start_sec 对齐事件时刻，标记 nel_aligned；
    - 无事件但有 BPM → 节拍吸附（偏差超限不吸）；
    - 返回 {aligned, beat_snapped} 统计。
    """
    from clipwright.schema.timeline import ClipKind

    nel, bpm = timeline_nel_bpm(timeline)
    stats = {"aligned": 0, "beat_snapped": 0}
    if not nel and not bpm:
        return stats
    for t in getattr(timeline, "tracks", []) or []:
        if t.kind != ClipKind.ANIMATION:
            continue
        for c in list(t.clips or []):
            md = getattr(c, "metadata", {}) or {}
            if md.get("renderer") not in ("mg_hyperframes",):
                continue
            start = float(getattr(c, "start_sec", 0) or 0)
            dur = float(getattr(c, "duration_sec", 3) or 3)
            ev = pick_nel_event(nel, start, start + dur, prefer=prefer, max_shift=max_shift) if nel else None
            if ev:
                c.start_sec = round(float(ev["t"]), 3)
                md["nel_aligned"] = True
                md["nel_cue"] = ev.get("payload", "")
                md["nel_type"] = ev.get("type", "")
                stats["aligned"] += 1
                continue
            if bpm:
                snapped = snap_to_beat(start, bpm)
                if abs(snapped - start) > 1e-6:
                    c.start_sec = snapped
                    md["beat_snapped"] = True
                    stats["beat_snapped"] += 1
    if stats["aligned"] or stats["beat_snapped"]:
        from clipwright.config import logger
        logger.info("NEL 对齐: %s", stats)
    return stats
