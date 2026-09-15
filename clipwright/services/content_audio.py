"""音频维度（R3）：音频事件检测 + 转写短语对齐到镜头 + 关键词抽取。

为什么音频层要单独做
--------------------
"素材里有什么"光看画面不够：**声音是最便宜的强信号**——掌声、欢呼、撞击、音乐起落、
静音段落，都能在不加载任何模型的前提下从 PCM 直接算出来（纯 numpy + 标准库 `wave`），
而且它们恰好是"剪辑切点"最常落的地方（V6 的短语打包也正是把静音区间单独保留成切点候选）。

三个能力：
1. `detect_audio_events`：短时能量 → 静音 / 大响段 / 起始沿（onset）；
2. `align_segments_to_shots`：任一来源的转写片段（whisper / 云端 / 插件后端）按**时间重叠**
   落到镜头上，并抽取关键词；
3. `keywords_from_text`：无分词器依赖的关键词抽取（CJK 二元组 + ASCII 词 + 域词表命中；
   若装了 jieba 会自动优先使用）。

**诚实边界**：
- 只做**能量类**事件。区分"人声/音乐/掌声"需要声学模型，本轮**不做**（因此事件类型里没有
  "speech"，只有可测量的 `silence` / `loud` / `onset`）；
- 本机**没有安装任何 STT 后端**（whisper/faster-whisper 都没有），因此"自动转录"这条链
  在本轮**未被真实跑通**：对齐层是用合成转写验证的，真实转写接入另需后端。
"""

from __future__ import annotations

import asyncio
import importlib.util
import math
import re
import subprocess
import wave
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 短时能量分析参数
WIN_MS = 25
HOP_MS = 10
#: 大响段判定：`ceil - max(loud_k×3dB, 25%·span)`（ceil = p95）
LOUD_K = 3.5
#: 静音判定：`floor + max(3dB, 15%·span)`（floor = p10）
SILENCE_FLOOR_DB = 6.0
#: 起始沿判定：短窗内上升 ≥ max(8dB, 10%·动态范围)
ONSET_RISE_DB = 8.0
ONSET_WINDOW_SEC = 0.3
#: 动态范围下界：低于它就认为"没有动态范围"（恒定电平/数字静音）→ 不出任何事件
MIN_SPAN_DB = 3.0

#: 关键词抽取用的中文停用词（只列高频虚词，宁可漏也不要让噪声词进索引）
_STOPWORDS = {
    "这个", "那个", "我们", "你们", "他们", "就是", "然后", "因为", "所以", "如果",
    "但是", "而且", "还是", "可以", "什么", "怎么", "已经", "现在", "这样", "那样",
    "一个", "一下", "一些", "非常", "真的", "其实", "感觉", "知道", "觉得", "时候",
    "the", "and", "for", "with", "that", "this", "you", "are", "was", "have", "our",
}
_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'\-]{1,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


# ── 音频读取与能量 ────────────────────────────────────────


async def extract_audio_wav(
    media_path: str | Path,
    out_wav: str | Path,
    *,
    sample_rate: int = 16000,
    timeout: float = 300.0,
) -> Optional[Path]:
    """用 ffmpeg 抽单声道 WAV（后续纯 numpy 处理，不引入 librosa）。"""
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", str(int(sample_rate)), "-f", "wav", str(out),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _o, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("抽音轨超时: %s", media_path)
        return None
    except FileNotFoundError:
        logger.warning("ffmpeg 不可用，跳过音轨抽取: %s", media_path)
        return None
    except Exception as exc:
        logger.warning("抽音轨失败: %s", exc)
        return None
    if proc.returncode != 0 or not out.exists():
        logger.info("素材无音轨或抽取失败（跳过音频层）: %s", Path(media_path).name)
        return None
    return out


def read_wav_mono(path: str | Path) -> tuple[Any, int]:
    """读单声道 WAV → (float32 数组 ∈ [-1,1], 采样率)。失败返回 (None, 0)。"""
    try:
        import numpy as np

        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        if width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
        elif width == 1:
            data = (np.frombuffer(raw, dtype="<u1").astype("float32") - 128.0) / 128.0
        elif width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype("float32") / 2147483648.0
        else:
            return (None, 0)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return (data, sr)
    except Exception as exc:
        logger.debug("WAV 读取失败: %s", exc)
        return (None, 0)


def frame_energy_db(signal: Any, sample_rate: int,
                    win_ms: int = WIN_MS, hop_ms: int = HOP_MS) -> tuple[Any, float]:
    """短时 RMS → dB（返回 (每帧 dB 数组, 帧步长秒)）。"""
    import numpy as np

    if signal is None or len(signal) == 0 or sample_rate <= 0:
        return (np.zeros(0, dtype="float32"), hop_ms / 1000.0)
    win = max(1, int(sample_rate * win_ms / 1000))
    hop = max(1, int(sample_rate * hop_ms / 1000))
    n = 1 + max(0, (len(signal) - win)) // hop
    db = np.zeros(n, dtype="float32")
    eps = 1e-9
    for i in range(n):
        chunk = signal[i * hop: i * hop + win]
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        db[i] = 20.0 * math.log10(max(rms, eps))
    return (db, hop / float(sample_rate))


def _median_mad(values: Any) -> tuple[float, float]:
    """中位数 + MAD（保留：分位数阈值在小样本上仍可参考它做诊断）。"""
    import numpy as np

    if values is None or len(values) == 0:
        return (0.0, 0.0)
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return (med, mad)


def _percentile(values: Any, q: float) -> float:
    try:
        import numpy as np

        return float(np.percentile(values, q)) if len(values) else 0.0
    except Exception:  # pragma: no cover
        return 0.0


def detect_audio_events(
    signal: Any,
    sample_rate: int,
    *,
    min_event_sec: float = 0.3,
    loud_k: float = LOUD_K,
    silence_floor_db: float = 6.0,
    onset_rise_db: float = ONSET_RISE_DB,
) -> list[dict[str, Any]]:
    """能量类音频事件：`silence` / `loud` / `onset`。

    阈值用**分位数**自适应（floor=p10、ceil=p95），不用"中位数 + MAD"：
    实测数字静音素材里中位数会被静音段主导（floor 与 median 重合），
    导致静音阈值落在噪声地板之下而**检测不到静音**；分位数对双峰分布稳定得多。

    阈值定义：
    - `span = p95 - p10`
    - 静音：`db < p10 + max(3dB, 15%·span)` 且持续 ≥ `min_event_sec`
    - 大响段：`db > p95 - max(loud_k·3dB, 25%·span)` 且持续 ≥ `min_event_sec`
    恒定电平（span≈0）时两者都不会命中——没有动态范围就没有"事件"，这是正确的。
    """
    import numpy as np

    db, hop = frame_energy_db(signal, sample_rate)
    if db.size == 0:
        return []
    floor = _percentile(db, 10)
    ceil = _percentile(db, 95)
    span = max(0.0, ceil - floor)
    if span < MIN_SPAN_DB:
        # 恒定电平（含整段数字静音/纯音）：没有动态范围就没有"事件"。
        # 全静音这类**素材级**属性应由 speech/统计层表达，硬造一个事件只会污染索引。
        return []
    silence_thr = floor + max(3.0, 0.15 * span)
    loud_thr = ceil - max(loud_k * 3.0, 0.25 * span)
    scale = span or 1.0
    events: list[dict[str, Any]] = []

    def _runs(mask: Any) -> Iterable[tuple[int, int]]:
        start = None
        for i, flag in enumerate(mask):
            if flag and start is None:
                start = i
            elif not flag and start is not None:
                yield (start, i)
                start = None
        if start is not None:
            yield (start, len(mask))

    min_frames = max(1, int(min_event_sec / hop)) if hop > 0 else 1

    for s, e in _runs(db > loud_thr):
        if e - s < min_frames:
            continue
        events.append({
            "kind": "loud",
            "t0": round(s * hop, 3),
            "t1": round(e * hop, 3),
            "peak_db": round(float(db[s:e].max()), 2),
            "mean_db": round(float(db[s:e].mean()), 2),
            "evidence": "short_term_rms",
        })

    for s, e in _runs(db < silence_thr):
        if e - s < min_frames:
            continue
        # 首尾静音也算事件（V6 把首/中/尾静音都当切点候选）
        events.append({
            "kind": "silence",
            "t0": round(s * hop, 3),
            "t1": round(e * hop, 3),
            "mean_db": round(float(db[s:e].mean()), 2),
            "evidence": "short_term_rms",
        })

    # 起始沿：短窗内能量突升（掌声/撞击/口号）
    rise_frames = max(1, int(ONSET_WINDOW_SEC / hop)) if hop > 0 else 1
    if db.size > rise_frames:
        delta = db[rise_frames:] - db[:-rise_frames]
        for idx in np.where(delta >= max(onset_rise_db, 0.1 * scale))[0]:
            t = round(float(idx + rise_frames) * hop, 3)
            if events and any(ev["kind"] == "onset" and abs(ev["t0"] - t) < 0.2 for ev in events):
                continue
            events.append({
                "kind": "onset",
                "t0": t,
                "t1": round(t + ONSET_WINDOW_SEC, 3),
                "rise_db": round(float(delta[idx]), 2),
                "evidence": "short_term_rms",
            })

    events.sort(key=lambda ev: (ev["t0"], ev["kind"]))
    summary = {"floor_db": round(floor, 2), "ceil_db": round(ceil, 2),
               "silence_thr_db": round(silence_thr, 2), "loud_thr_db": round(loud_thr, 2),
               "frames": int(db.size), "hop_sec": round(hop, 4)}
    for ev in events:
        ev["thresholds"] = summary
    return events


def merge_events(events: list[dict[str, Any]], max_gap: float = 0.2) -> list[dict[str, Any]]:
    """合并时间上几乎相连的同类型事件（避免碎成一串）。"""
    out: list[dict[str, Any]] = []
    for ev in sorted(events, key=lambda e: (e.get("t0", 0.0), e.get("kind", ""))):
        if (out and out[-1]["kind"] == ev["kind"]
                and float(ev["t0"]) - float(out[-1].get("t1", 0.0)) <= max_gap):
            out[-1]["t1"] = max(float(out[-1].get("t1", 0.0)), float(ev.get("t1", 0.0)))
            out[-1]["merged"] = int(out[-1].get("merged", 1)) + 1
            continue
        out.append(dict(ev))
    return out


# ── 转写片段对齐 ──────────────────────────────────────────


def segments_to_phrases(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """归一化任一来源的转写片段 → `[{t0,t1,text,speaker}]`（键名容错）。"""
    out: list[dict[str, Any]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or seg.get("content") or seg.get("word") or "").strip()
        if not text:
            continue
        try:
            t0 = float(seg.get("start", seg.get("start_sec", seg.get("from", 0.0))) or 0.0)
            t1 = float(seg.get("end", seg.get("end_sec", seg.get("to", t0))) or t0)
        except (TypeError, ValueError):
            continue
        if t1 < t0:
            t0, t1 = t1, t0
        out.append({"t0": round(t0, 3), "t1": round(t1, 3), "text": text,
                    "speaker": str(seg.get("speaker") or seg.get("spk") or "")})
    return sorted(out, key=lambda p: p["t0"])


def align_segments_to_shots(
    segments: list[dict[str, Any]],
    shot_ranges: list[tuple[float, float]],
    *,
    lexicon: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把转写片段按**时间重叠**落到镜头上。

    返回 `(每个镜头的语音信息, 对齐统计)`。重叠判定用"交集中点落在哪个镜头"：
    转写片段跨越多个镜头时，**按交集长度切分**给各镜头（比整段丢给一个镜头准确）。
    """
    phrases = segments_to_phrases(segments)
    per_shot: list[dict[str, Any]] = [
        {"text": "", "phrases": [], "keywords": [], "speaker": ""} for _ in shot_ranges
    ]
    matched = 0
    for ph in phrases:
        assigned = False
        for i, (s0, s1) in enumerate(shot_ranges):
            lo, hi = max(ph["t0"], s0), min(ph["t1"], s1)
            if hi - lo <= 0.02:
                continue
            entry = {"t0": round(lo - s0, 3), "t1": round(hi - s0, 3),
                     "abs_t0": lo, "abs_t1": hi, "text": ph["text"]}
            per_shot[i]["phrases"].append(entry)
            per_shot[i]["text"] = (per_shot[i]["text"] + " " + ph["text"]).strip()
            if ph["speaker"] and not per_shot[i]["speaker"]:
                per_shot[i]["speaker"] = ph["speaker"]
            assigned = True
        matched += 1 if assigned else 0
    for entry in per_shot:
        entry["keywords"] = keywords_from_text(entry["text"], lexicon=lexicon)
    stats = {
        "phrases": len(phrases),
        "matched": matched,
        "unmatched": len(phrases) - matched,
        "shots_with_speech": sum(1 for e in per_shot if e["text"]),
    }
    return per_shot, stats


async def align_transcript_to_shots(
    media_path: str | Path,
    shot_ranges: list[tuple[float, float]],
    *,
    language: str = "zh",
    lexicon: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """自动转录（若有后端）→ 对齐。**无 STT 后端时返回空结果并记录原因**。"""
    work = Path(media_path)
    wav = await extract_audio_wav(work, work.parent / f"{work.stem}.asr.wav")
    if wav is None:
        return ([{"text": "", "phrases": [], "keywords": [], "speaker": ""} for _ in shot_ranges],
                {"phrases": 0, "matched": 0, "unmatched": 0, "shots_with_speech": 0,
                 "error": "no_audio_or_extract_failed"})
    try:
        from clipwright.services.stt import STTService

        result = await STTService().transcribe(str(wav), language=language)
    except Exception as exc:
        logger.info("转写不可用（跳过语音层）: %s", exc)
        return ([{"text": "", "phrases": [], "keywords": [], "speaker": ""} for _ in shot_ranges],
                {"phrases": 0, "matched": 0, "unmatched": 0, "shots_with_speech": 0,
                 "error": f"stt_unavailable:{type(exc).__name__}"})
    segments = list((result or {}).get("segments") or [])
    if not segments:
        err = str((result or {}).get("error") or "no_segments")
        return ([{"text": "", "phrases": [], "keywords": [], "speaker": ""} for _ in shot_ranges],
                {"phrases": 0, "matched": 0, "unmatched": 0, "shots_with_speech": 0,
                 "error": err[:120]})
    return align_segments_to_shots(segments, shot_ranges, lexicon=lexicon)


# ── 关键词抽取 ────────────────────────────────────────────


def keywords_from_text(
    text: str,
    *,
    lexicon: dict[str, list[str]] | None = None,
    limit: int = 12,
) -> list[str]:
    """无分词器依赖的关键词抽取。

    顺序与理由：
    1. **域词表命中优先**（最长匹配）——这些词正是用户会问的东西（"进球""开箱""价格"），
       比统计词更值得进索引；
    2. 装了 `jieba` 就用它分词（可选增强，不强依赖）；
    3. 否则 CJK 走**二元组**（中文的实用近似），ASCII 走词形匹配。

    局限（如实）：二元组会产出"比赛进"这种跨界词；没有 TF-IDF/停用词表规模支撑，
    因此关键词只用于**召回**，最终排序交给 R4 的向量 + rerank。
    """
    text = (text or "").strip()
    if not text:
        return []
    found: list[str] = []
    if lexicon:
        hits: list[tuple[int, str]] = []
        for _domain, words in lexicon.items():
            for w in words or []:
                w = str(w).strip()
                if w and w in text:
                    hits.append((text.index(w), w))
        for _pos, w in sorted(hits, key=lambda t: (-len(t[1]), t[0])):
            if w not in found:
                found.append(w)

    if importlib.util.find_spec("jieba") is not None:  # pragma: no cover - 可选增强
        try:
            import jieba

            for tok in jieba.cut(text):
                tok = tok.strip()
                if len(tok) >= 2 and tok not in _STOPWORDS and tok not in found:
                    found.append(tok)
        except Exception:
            pass
    else:
        for run in _CJK_RUN_RE.findall(text):
            if len(run) < 2:
                continue
            for i in range(len(run) - 1):
                bigram = run[i:i + 2]
                if bigram in _STOPWORDS or bigram in found:
                    continue
                found.append(bigram)

    for word in _ASCII_WORD_RE.findall(text):
        low = word.lower()
        if len(low) >= 3 and low not in _STOPWORDS and low not in found:
            found.append(low)
    return found[:max(1, limit)]


def summarize_audio_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """事件统计（写进摘要，让 Agent 一眼看到"这段素材响过几次"）。"""
    counts: dict[str, int] = {}
    for ev in events or []:
        kind = str(ev.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "total": len(events or []),
        "by_kind": counts,
        "first_loud": next((ev["t0"] for ev in events or [] if ev.get("kind") == "loud"), None),
    }


def events_for_shot(events: list[dict[str, Any]], t0: float, t1: float) -> list[dict[str, Any]]:
    """取与某个镜头有时间重叠的音频事件（相对时间一并给出，便于直接切）。"""
    out: list[dict[str, Any]] = []
    for ev in events or []:
        e0, e1 = float(ev.get("t0", 0.0)), float(ev.get("t1", ev.get("t0", 0.0)))
        lo, hi = max(e0, t0), min(e1, t1)
        if hi - lo <= 0.0:
            continue
        item = dict(ev)
        item["rel_t0"] = round(lo - t0, 3)
        item["rel_t1"] = round(hi - t0, 3)
        out.append(item)
    return out


def _dummy_ffprobe_guard() -> None:  # pragma: no cover - 显式保留 subprocess 依赖
    return subprocess.run(["ffmpeg", "-version"], capture_output=True)  # noqa: S603
