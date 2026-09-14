"""短语级转写打包（V6）——把逐词/逐句转写压成"短语视图"喂给 LLM。

为什么需要
----------
ASR 的原始产物是**逐词**（或逐句）JSON：每个单元都带 start/end/confidence，
直接塞进 prompt 会占掉大部分上下文（一小时素材的逐词 JSON 可达上百 KB），
模型真正需要的是"谁在什么时候说了什么话"，而不是每个词的时间戳。

打包规则（与 video-use 的 `helpers/pack_transcripts.py` 同思路）：
- 静音 ≥ `gap_sec`（默认 0.5s）断句；
- 换人（speaker 变化）断句；
- 单条短语超长（时长/字数上限）断句——避免长句挤成一行读不动；
- 输出 `[start-end] S1 文本` 紧凑视图，**静音区间单独保留**作为切点候选
  （切点最容易落在这里，白丢就浪费了）。

压缩量的度量来自 `TranscriptPack.compression`：它是**打包文本字符数**与
**原始逐词 JSON 字符数**之比，用于验收"prompt token 下降 ≥60%"这类目标。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

#: 断句的静音阈值（秒）
DEFAULT_GAP_SEC = 0.5
#: 单条短语时长上限（秒）
DEFAULT_MAX_DURATION = 6.0
#: 单条短语字数上限（CJK 按字符计，西文按"词"折算，见 `estimate_tokens`）
DEFAULT_MAX_CHARS = 48
#: 静音窗口的最短时长（低于此值只是自然停顿，不当作切点候选）
DEFAULT_MIN_SILENCE = 0.5


def estimate_tokens(text: str) -> float:
    """粗略估算 token 数（不引依赖）。

    CJK 字符约 1 token/字；连续西文按 ~4 字符/token。用于比较"打包前后"的量级，
    不用于计费——量级对就够了（两个文本用同一个估算函数，比值是可信的）。
    """
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    rest = re.sub(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", " ", text)
    latin_chars = len(re.findall(r"[A-Za-z0-9']+", rest))
    other = len(re.findall(r"[^\sA-Za-z0-9']", rest))
    return cjk + latin_chars * 1.3 + other * 0.5


@dataclass
class TranscriptUnit:
    """转写单元（一个词或一句话）。"""
    text: str
    start: float
    end: float
    speaker: str = ""
    confidence: float | None = None


@dataclass
class TranscriptPhrase:
    """打包后的短语。"""
    start: float
    end: float
    text: str
    speaker: str = ""
    units: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "speaker": self.speaker,
            "units": self.units,
        }


@dataclass
class TranscriptPack:
    """打包结果 + 压缩度量。"""
    phrases: list[TranscriptPhrase] = field(default_factory=list)
    silences: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    raw_chars: int = 0
    packed_chars: int = 0

    @property
    def compression(self) -> float:
        """打包后字符数 / 原始 JSON 字符数（越小越省）。原始为空时返回 0。"""
        if self.raw_chars <= 0:
            return 0.0
        return self.packed_chars / self.raw_chars

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "phrases": [p.to_dict() for p in self.phrases],
            "silences": self.silences,
            "phrase_count": len(self.phrases),
            "silence_count": len(self.silences),
            "raw_chars": self.raw_chars,
            "packed_chars": self.packed_chars,
            "compression": round(self.compression, 4),
        }


def _coerce_units(units: Iterable[dict[str, Any] | TranscriptUnit]) -> list[TranscriptUnit]:
    """把外部数据（逐词 JSON / 逐句 segments）归一成 `TranscriptUnit` 列表（按时间排序）。

    兼容的键名：文本 `text`/`word`/`content`；时间 `start`/`start_sec`/`from`，
    `end`/`end_sec`/`to`；说话人 `speaker`/`spk`/`channel`。
    """
    out: list[TranscriptUnit] = []
    for raw in units or []:
        if isinstance(raw, TranscriptUnit):
            out.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or raw.get("word") or raw.get("content") or "").strip()
        if not text:
            continue
        try:
            start = float(raw.get("start", raw.get("start_sec", raw.get("from", 0))) or 0)
            end = float(raw.get("end", raw.get("end_sec", raw.get("to", start))) or start)
        except (TypeError, ValueError):
            continue
        if end < start:
            start, end = end, start
        speaker = str(raw.get("speaker") or raw.get("spk") or raw.get("channel") or "")
        conf = raw.get("confidence")
        try:
            confidence = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        out.append(TranscriptUnit(text=text, start=start, end=end, speaker=speaker, confidence=confidence))
    out.sort(key=lambda u: (u.start, u.end))
    return out


def pack_phrases(
    units: Iterable[dict[str, Any] | TranscriptUnit],
    *,
    gap_sec: float = DEFAULT_GAP_SEC,
    max_duration: float = DEFAULT_MAX_DURATION,
    max_chars: int = DEFAULT_MAX_CHARS,
    joiner: str = "",
) -> list[TranscriptPhrase]:
    """把转写单元合并成短语（静音 / 换人 / 超长三处断句）。"""
    coerced = _coerce_units(units)
    phrases: list[TranscriptPhrase] = []
    current: TranscriptPhrase | None = None

    for unit in coerced:
        if current is None:
            current = TranscriptPhrase(start=unit.start, end=unit.end, text=unit.text,
                                       speaker=unit.speaker, units=1)
            continue

        gap = unit.start - current.end
        speaker_changed = bool(unit.speaker) and bool(current.speaker) and unit.speaker != current.speaker
        would_exceed_duration = (unit.end - current.start) > max_duration
        would_exceed_chars = (len(current.text) + len(unit.text) + len(joiner)) > max_chars

        if gap >= gap_sec or speaker_changed or would_exceed_duration or would_exceed_chars:
            phrases.append(current)
            current = TranscriptPhrase(start=unit.start, end=unit.end, text=unit.text,
                                       speaker=unit.speaker, units=1)
            continue

        current.text = f"{current.text}{joiner}{unit.text}"
        current.end = max(current.end, unit.end)
        current.units += 1
        if not current.speaker and unit.speaker:
            current.speaker = unit.speaker

    if current is not None:
        phrases.append(current)
    return phrases


def silence_windows(
    units: Iterable[dict[str, Any] | TranscriptUnit],
    *,
    min_gap: float = DEFAULT_MIN_SILENCE,
    total_duration: float | None = None,
) -> list[dict[str, Any]]:
    """静音区间（切点候选）。

    返回 `[{start, end, duration}]`：单元之间的静音 + （给了总时长时）首尾静音。
    首尾静音同样算切点候选——开头的沉默和结尾的拖尾都是明显的剪辑位置。
    """
    coerced = _coerce_units(units)
    windows: list[dict[str, Any]] = []
    if not coerced:
        return windows

    if coerced[0].start >= min_gap:
        windows.append({"start": 0.0, "end": round(coerced[0].start, 2),
                        "duration": round(coerced[0].start, 2), "where": "head"})
    for prev, cur in zip(coerced, coerced[1:]):
        gap = cur.start - prev.end
        if gap >= min_gap:
            windows.append({"start": round(prev.end, 2), "end": round(cur.start, 2),
                            "duration": round(gap, 2), "where": "between"})
    if total_duration is not None:
        tail = float(total_duration) - coerced[-1].end
        if tail >= min_gap:
            windows.append({"start": round(coerced[-1].end, 2), "end": round(float(total_duration), 2),
                            "duration": round(tail, 2), "where": "tail"})
    return windows


def render_packed(phrases: Sequence[TranscriptPhrase], *, digits: int = 1) -> str:
    """渲染紧凑短语视图：`[start-end] S1 文本`（speaker 为空时不显示）。"""
    lines: list[str] = []
    for phrase in phrases:
        span = f"[{phrase.start:.{digits}f}-{phrase.end:.{digits}f}]"
        speaker = f" {phrase.speaker}" if phrase.speaker else ""
        lines.append(f"{span}{speaker} {phrase.text}")
    return "\n".join(lines)


def pack_transcript(
    units: Iterable[dict[str, Any] | TranscriptUnit],
    *,
    gap_sec: float = DEFAULT_GAP_SEC,
    max_duration: float = DEFAULT_MAX_DURATION,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_silence: float = DEFAULT_MIN_SILENCE,
    total_duration: float | None = None,
    raw_payload: Any = None,
    speaker_of: Callable[[TranscriptUnit], str] | None = None,
) -> TranscriptPack:
    """一站式打包：短语视图 + 静音窗口 + 压缩度量。

    `raw_payload` 用于计算基线体积（默认把归一化后的单元按 JSON 序列化，
    即"原样塞进 prompt"的体积）；给什么都行，只要是**未打包**的表示。
    `speaker_of` 可在归一化后统一打标（例如按声道号映射到 S1/S2）。
    """
    units_list = _coerce_units(units)
    if speaker_of is not None:
        units_list = [
            TranscriptUnit(text=u.text, start=u.start, end=u.end,
                           speaker=speaker_of(u) or u.speaker, confidence=u.confidence)
            for u in units_list
        ]
    phrases = pack_phrases(units_list, gap_sec=gap_sec,
                           max_duration=max_duration, max_chars=max_chars)
    text = render_packed(phrases)
    baseline = raw_payload if raw_payload is not None else [
        {"text": u.text, "start": u.start, "end": u.end, "speaker": u.speaker, "confidence": u.confidence}
        for u in units_list
    ]
    try:
        raw_chars = len(json.dumps(baseline, ensure_ascii=False))
    except (TypeError, ValueError):
        raw_chars = len(str(baseline))
    return TranscriptPack(
        phrases=phrases,
        silences=silence_windows(units_list, min_gap=min_silence, total_duration=total_duration),
        text=text,
        raw_chars=raw_chars,
        packed_chars=len(text),
    )


def pack_segments(segments: Iterable[dict[str, Any]], **kwargs: Any) -> TranscriptPack:
    """便捷入口：与 `STTResult.segments`（`[{start,end,text,confidence}]`）直接对接。"""
    return pack_transcript(segments, **kwargs)
