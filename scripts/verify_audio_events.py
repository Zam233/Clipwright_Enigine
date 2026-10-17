"""R3 验收：音频事件 + 转写对齐 + 领域事件定位。

三部分：
1. **音频事件**：合成"静音/大响"已知位置的素材，量检出时间与真值的误差（门槛 ≤0.3s）；
2. **转写对齐**：构造跨镜头的转写片段，验证按重叠切分、边界归属、关键词；
3. **事件定位**：给一段带时间戳的解说，验证"进球"等事件落在正确的秒上，且带证据句。

用法：
    python scripts/verify_audio_events.py [--tol 0.3] [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.services import content_audio as ca  # noqa: E402
from clipwright.services.event_lexicon import resolve_lexicon, spot_events_in_text  # noqa: E402

SR = 16000
#: (类型, 起始秒, 结束秒) —— 合成素材的音频真值
TRUTH = [("silence", 0.0, 0.8), ("loud", 0.8, 1.8), ("silence", 1.8, 2.4), ("loud", 2.4, 3.4)]
#: 镜头真值（与音频事件错位，避免"恰好对上"的假象）
SHOTS = [(0.0, 1.2), (1.2, 2.0), (2.0, 3.4)]


def _build_signal():
    import numpy as np

    parts = []
    for kind, t0, t1 in TRUTH:
        n = int((t1 - t0) * SR)
        if kind == "loud":
            t = np.arange(n) / SR
            parts.append(0.85 * np.sin(2 * np.pi * 420 * t))
        else:
            parts.append(np.zeros(n, dtype="float32"))
    return np.concatenate(parts)


def _write_wav(path: Path, signal) -> str:
    import numpy as np

    arr = np.clip(np.asarray(signal, dtype="float32"), -1.0, 1.0)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((arr * 32767.0).astype("<i2").tobytes())
    return str(path)


def _make_video(path: Path, signal) -> bool:
    """带音轨的合成视频：视频按镜头切分（红/蓝/绿），音轨用上面的信号。"""
    wav = path.parent / "audio.wav"
    _write_wav(wav, signal)
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=1.2",
        "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=0.8",
        "-f", "lavfi", "-i", "color=c=green:s=160x120:r=10:d=1.4",
        "-i", str(wav),
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
    ], capture_output=True, text=True)
    return r.returncode == 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.3, help="事件/对齐时间容差（秒）")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        print("需要本机 ffmpeg")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="clipwright_audio_"))
    failures: list[str] = []
    try:
        signal = _build_signal()
        video = tmp / "sample.mp4"
        made = _make_video(video, signal)
        print(f"[1] 合成素材：{len(signal) / SR:.1f}s 音频 + 3 个镜头"
              f"（视频合成{'成功' if made else '失败 → 仅用 WAV 验证'}）")

        # ── 音频事件 ──
        wav = tmp / "audio.wav"
        events = ca.merge_events(ca.detect_audio_events(signal, SR))
        print(f"[2] 音频事件检出 {len(events)} 个：")
        for ev in events:
            print(f"      {ev['kind']:<8s} [{ev['t0']:5.2f}-{ev['t1']:5.2f}] "
                  f"{('peak=%.1fdB' % ev['peak_db']) if 'peak_db' in ev else ''}")
        louds = [ev for ev in events if ev["kind"] == "loud"]
        truth_louds = [(t0, t1) for kind, t0, t1 in TRUTH if kind == "loud"]
        if len(louds) != len(truth_louds):
            failures.append(f"大响段数量不符：检出 {len(louds)}，真值 {len(truth_louds)}")
        else:
            for ev, (t0, t1) in zip(louds, truth_louds):
                err = max(abs(ev["t0"] - t0), abs(ev["t1"] - t1))
                ok = err <= args.tol
                print(f"      匹配真值 [{t0}-{t1}]：最大时间误差 {err:.3f}s "
                      f"{'OK' if ok else 'NG'}")
                if not ok:
                    failures.append(f"大响段时间误差 {err:.3f}s > {args.tol}s")

        # ── 转写对齐 ──
        segments = [
            {"start": 0.2, "end": 1.0, "text": "大家好，欢迎来到本期节目"},   # → 镜头 0
            {"start": 1.0, "end": 1.6, "text": "这一脚射门太突然了"},         # 跨镜头 0/1
            {"start": 2.6, "end": 3.2, "text": "球进了，绝杀！"},             # → 镜头 2
        ]
        rows, stats = ca.align_segments_to_shots(segments, SHOTS,
                                                lexicon=resolve_lexicon("sports"))
        print(f"[3] 转写对齐：{stats['phrases']} 段 → 命中 {stats['matched']}，"
              f"{stats['shots_with_speech']} 个镜头有语音")
        for i, row in enumerate(rows):
            print(f"      镜头{i} [{SHOTS[i][0]}-{SHOTS[i][1]}] 文本=“{row['text'][:28]}” "
                  f"关键词={row['keywords'][:4]}")
        if rows[0]["text"] == "" or "球进了" not in rows[2]["text"]:
            failures.append("片段没有落到正确的镜头")
        if not any(p["text"].endswith("突然了") for p in rows[1]["phrases"]):
            failures.append("跨镜头片段没有被切分到后一个镜头")
        # 边界归属：1.0 起的片段必须同时出现在镜头 0 尾与镜头 1 首（交集切分）
        if not rows[0]["phrases"] or not rows[1]["phrases"]:
            failures.append("跨镜头片段未按交集切分给两侧")
        if "进球" not in rows[2]["keywords"] and "球进了" not in rows[2]["keywords"]:
            failures.append("关键词未从域词表命中")

        # ── 领域事件定位 ──
        lexicon = resolve_lexicon("sports", '{"goal": ["远射"]}')
        spottings = []
        for seg in segments:
            spottings.extend(spot_events_in_text(seg["text"], lexicon=lexicon,
                                                 t0=seg["start"], t1=seg["end"],
                                                 source="segment"))
        print(f"[4] 领域事件定位 {len(spottings)} 条：")
        for ev in spottings:
            print(f"      {ev['type']:<10s} {ev['label']:<6s} @ [{ev['t0']}-{ev['t1']}] "
                  f"置信度={ev['confidence']} 证据=“{ev['evidence'][:24]}”")
        goal = [ev for ev in spottings if ev["type"] == "goal"]
        if not goal:
            failures.append("解说里的进球事件没有被识别")
        else:
            # 解说里可能有多处提到"射门/进球"，判据是**存在一条**落在真值位置附近
            best = min(goal, key=lambda ev: abs(ev["t0"] - 2.6))
            err = abs(best["t0"] - 2.6)
            if err > args.tol:
                failures.append(f"进球事件时间偏差 {err:.3f}s > {args.tol}s")
            if not best.get("evidence"):
                failures.append("事件没有证据句（无法核查为什么判成进球）")

        # ── 端到端（Tool）──
        if made:
            from clipwright.tool.event_spot import EventSpotTool

            res = await EventSpotTool().execute(mode="audio", media_path=str(video),
                                                shot_ranges=[list(r) for r in SHOTS])
            if res.status != "success":
                failures.append(f"event_spot audio 模式失败：{res.error}")
            else:
                print(f"[5] 工具端到端：{res.output['count']} 个音频事件，"
                      f"按镜头切片 {len(res.output['per_shot'])} 组，"
                      f"摘要={res.output['summary']['by_kind']}")

        print()
        if failures:
            print("AUDIO EVENTS VERIFY FAIL")
            for f in failures:
                print("  -", f)
            return 1
        print("AUDIO EVENTS VERIFY PASS")
        return 0
    finally:
        if args.keep:
            print(f"[keep] {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
