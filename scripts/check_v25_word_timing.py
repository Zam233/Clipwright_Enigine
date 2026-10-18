"""V25 逐词误差：两条判定路都要有证据。

PRD 的 V25 是"逐词误差 ≤42ms **或** 标 init 并列入待确认清单"。所以本脚本分两段：

**A. 实测起音链**（能量法）—— 造一条**真值精确**的点击轨（每 0.5 秒一击，宽 12ms），
用仓库自带的 `content_audio.detect_audio_events` 检出 onset，与真值比误差。这一步量的是
"检测器准不准"，不是"snap 数学准不准"（后者拿真值直接喂必然为 0，没有意义）。

**B. 无实测数据时** —— 用歌词生成 cues（不传 onsets），断言每条都标 `init`，并且
`pending_confirmation()` 把它们逐条列出（"没验证过的项要显式列出来，而不是假装准"）。

用法::

    python scripts/check_v25_word_timing.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 点击轨参数（真值由采样率与间隔直接算出，不依赖检测器）
CLICK_INTERVAL = 0.5
CLICK_COUNT = 8
CLICK_WIDTH = 0.012
#: 第一击不从 t=0 起：能量法检测器需要"前导静音"才能看到上升沿，t=0 起点不可测
CLICK_LEAD_IN = 0.2
TOLERANCE_SEC = 0.042  # 42ms
SAMPLE_RATE = 44100


def _make_click_track(path: Path) -> list[float]:
    """用 numpy/scipy 直接合成点击轨（真值由采样点算出，不依赖 ffmpeg 表达式）。

    第一版用 ffmpeg `mod(t-0.2, 0.5)` 生成：负数取模的行为把开头变成了一段长音，
    真值与音频对不上——所以这里改成逐击写入，真值精确到采样。
    """
    import numpy as np
    from scipy.io import wavfile

    path.parent.mkdir(parents=True, exist_ok=True)
    duration = CLICK_LEAD_IN + CLICK_INTERVAL * CLICK_COUNT + 0.2
    total = int(duration * SAMPLE_RATE)
    signal = np.zeros(total, dtype=np.float64)
    onsets: list[float] = []
    for index in range(CLICK_COUNT):
        start = CLICK_LEAD_IN + index * CLICK_INTERVAL
        i0 = int(round(start * SAMPLE_RATE))
        length = int(round(CLICK_WIDTH * SAMPLE_RATE))
        local = np.arange(length) / SAMPLE_RATE
        # 指数衰减包络，避免裁切处的爆音把"上升沿"搅乱
        signal[i0:i0 + length] = 0.9 * np.sin(2 * np.pi * 1000 * local) * np.exp(-local * 80)
        onsets.append(round(start, 6))
    wavfile.write(str(path), SAMPLE_RATE, (np.clip(signal, -1, 1) * 32767).astype(np.int16))
    return onsets


async def _detect_onsets(wav: Path, work_dir: Path) -> list[float]:
    from clipwright.services.content_audio import (
        detect_audio_events,
        extract_audio_wav,
        read_wav_mono,
    )

    mono_path = await extract_audio_wav(wav, work_dir / "clicks_mono.wav", sample_rate=SAMPLE_RATE)
    if mono_path is None:
        raise RuntimeError("抽音轨失败")
    signal, rate = read_wav_mono(mono_path)
    events = detect_audio_events(signal, rate)
    return sorted(float(event["t0"]) for event in events if event.get("kind") == "onset")


def _errors(truth: list[float], detected: list[float]) -> list[float]:
    """每个真值起音找最近的检测值，返回误差列表（检测漏掉的记为无穷大）。"""
    result: list[float] = []
    for t in truth:
        if not detected:
            result.append(float("inf"))
            continue
        nearest = min(detected, key=lambda d: abs(d - t))
        # 距离超过半拍就是没检测到，不该算"误差 0"
        result.append(abs(nearest - t) if abs(nearest - t) < CLICK_INTERVAL / 2 else float("inf"))
    return result


def _part_b() -> dict:
    from clipwright.schema.lyrics import LyricLine, LyricWord, LyricsDoc
    from clipwright.services.mv_cues import build_grid, cues_from_lyrics

    lyrics = LyricsDoc(lines=[
        LyricLine(i=0, t0=0.5, t1=2.0, text_ref="text(0)",
                  words=[LyricWord(w=0, t0=0.5, t1=1.0, key=True)], syllables=[0.5, 0.9, 1.3]),
        LyricLine(i=1, t0=2.0, t1=3.5, text_ref="text(1)",
                  words=[LyricWord(w=1, t0=2.0, t1=2.6, key=True)], syllables=[2.0, 2.4, 2.8]),
    ])
    grid = build_grid(120.0, beat_zero_sec=0.0)
    cues = cues_from_lyrics(lyrics, grid=grid)
    from clipwright.services.mv_cues import CuesDoc

    doc = CuesDoc(grid=grid, items=cues)
    pending = doc.pending_confirmation()
    return {
        "cue_count": len(cues),
        "init_count": doc.init_count(),
        "all_init": doc.init_count() == len(cues),
        "pending_count": len(pending),
        "pending_sample": pending[:3],
        "ok": doc.init_count() == len(cues) and len(pending) == len(cues),
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V25：逐词误差 / init 待确认清单")
    parser.parse_args(argv)

    out_dir = ROOT / "out" / "v25"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "clicks.wav"
    truth = _make_click_track(wav)
    detected = await _detect_onsets(wav, out_dir)
    errors = _errors(truth, detected)
    finite = [e for e in errors if e != float("inf")]

    part_a = {
        "truth_onsets": truth,
        "detected_onsets": [round(t, 4) for t in detected],
        "detected_count": len(detected),
        "errors_ms": [round(e * 1000, 2) if e != float("inf") else None for e in errors],
        "max_error_ms": round(max(finite) * 1000, 2) if finite else None,
        "mean_error_ms": round(sum(finite) / len(finite) * 1000, 2) if finite else None,
        "tolerance_ms": TOLERANCE_SEC * 1000,
        "ok": bool(finite) and len(finite) == len(truth) and max(finite) <= TOLERANCE_SEC,
    }
    part_b = _part_b()

    report = {
        "ok": part_a["ok"] and part_b["ok"],
        "part_a_measured_onsets": part_a,
        "part_b_init_pending": part_b,
        "caveat": (
            "A 段用合成点击轨（真值精确）验证检测误差；真实音乐里的弱起、滑音未覆盖。"
            "B 段是无实测数据时的合规路径：全部标 init 并列入待确认清单。"
        ),
    }
    target = out_dir / "report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
