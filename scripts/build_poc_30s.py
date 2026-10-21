"""装配 30 秒 POC：三个画面各 ≥1 段，拼成一条，并自证确定性（V21）。

输入是**已经渲过**的三段确定性产物（都过了逐帧哈希闸）：

- ``out/mv_poc/chat_a.mov``        聊天界面叙事，960x540@30，12s
- ``out/plate_shot/a.mov``         版画镜头，960x540@30，6s
- ``out/archetypes/code_data/a.mov`` 代码/数据，960x540@30，8s

装配规则：12 + 8 + 10 = 30 秒（后两段用循环补足），统一 fps/像素格式后硬切拼接。
**这是"拼装"，不是"连续叙事"**：不做转场、不做卡点对齐——那是 R-05 的后续工作，
这里只回答 V21 的问题："30 秒里三个画面是不是各有一段、而且可复现"。

用法::

    python scripts/build_poc_30s.py
    python scripts/build_poc_30s.py --out out/poc30s
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (源文件, 截取起点秒, 目标时长秒, 画面类型说明)
SEGMENTS: list[tuple[str, float, float, str]] = [
    ("out/mv_poc/chat_a.mov", 0.0, 12.0, "界面叙事（chat）"),
    ("out/plate_shot/a.mov", 0.0, 8.0, "版画（plate）"),
    ("out/archetypes/code_data/a.mov", 0.0, 10.0, "代码/数据（code_data）"),
]
TOTAL_SEC = 30.0


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd[:6])}…\n{result.stderr[-1500:]}")


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate"),
        "duration_sec": round(float((data.get("format") or {}).get("duration") or 0.0), 3),
        "audio_streams": len(audio),
    }


def build(out_dir: Path, name: str) -> Path:
    """把三段拼成一条 30 秒文件（重编码成 h264/yuv420p，便于分发给非剪辑环境）。"""
    parts: list[str] = []
    labels: list[str] = []
    for index, (rel, start, dur, _label) in enumerate(SEGMENTS):
        src = ROOT / rel
        if not src.exists():
            raise FileNotFoundError(f"缺少源片段：{src}（先跑对应的渲染脚本）")
        parts.append(
            f"[{index}:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
            f"scale=960:540,fps=30,format=yuv420p[v{index}]"
        )
        labels.append(f"[v{index}]")
    filter_complex = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for rel, _start, _dur, _label in SEGMENTS:
        cmd += ["-stream_loop", "-1", "-i", str(ROOT / rel)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart", "-t", str(TOTAL_SEC),
        str(out_dir / name),
    ]
    _run(cmd)
    return out_dir / name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="装配 30 秒 POC（V21）")
    parser.add_argument("--out", default="out/poc30s")
    args = parser.parse_args(argv)

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    first = build(out_dir, "poc30s_a.mp4")
    second = build(out_dir, "poc30s_b.mp4")

    report: dict = {
        "ok": False,
        "segments": [
            {"source": rel, "screen": label, "seconds": dur}
            for rel, _s, dur, label in SEGMENTS
        ],
        "target_seconds": TOTAL_SEC,
        "a": _probe(first),
        "b": _probe(second),
    }

    # 确定性：两遍装配逐帧哈希必须一致
    from tools.hash_frames import _load, compare

    diff = compare(_load(first, 480, 270), _load(second, 480, 270))
    report["deterministic"] = diff.equal
    report["first_diff_frame"] = diff.first_diff
    report["frames_compared"] = diff.total_a

    # 闪光闸：成片不得有高频闪变
    from tools.check_flash import analyze_video

    flash = analyze_video(first, fps=30.0)
    report["flash_violations"] = list(flash.violations)
    report["flash_ok"] = flash.ok

    report["ok"] = (
        diff.equal
        and flash.ok
        and report["a"]["duration_sec"] >= TOTAL_SEC - 0.2
        and report["a"]["width"] == 960
        and len(SEGMENTS) >= 3
    )
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
