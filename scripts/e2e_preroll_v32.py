"""V32 真机验证：负时间片头（画面先起、音乐后进）到底有没有做对。

步骤：

1. 生成一段 2 秒正弦波当作"音乐"（放在 `_cache/v32/`，白名单目录内）；
2. 用 2 秒占位素材建一条时间线（画面 2 秒）；
3. `POST /api/render/queue`，带 `preroll_sec=1`；
4. 渲染完成后量成片时长，并用 `tools/check_sync.check_sync` 判定：
   期望 `视频 = 音频 + 片头`。

**口径**：只验"片头有没有把音画做同步"，不验画面好不好看。渲染走真 ffmpeg，慢。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TERMINAL = {"completed", "failed", "cancelled", "canceled"}
PREROLL_SEC = 1.0
SONG_SEC = 2.0
#: 两段用同一素材（复现"同键并发裁剪"，验证文件占用修复）
SAME_ASSET = False


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("未找到 ffmpeg")
    return exe


def _make_song(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return path


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return round(float((json.loads(out).get("format") or {}).get("duration") or 0.0), 3)


def _pick_placeholder(count: int = 2) -> list[Path]:
    candidates = sorted((ROOT / "_cache" / "tmp").glob("textvid_*.mp4"))
    if not candidates:
        raise RuntimeError("_cache/tmp 下没有占位视频，先跑一次管线或 POC 渲染")
    # 默认取不同素材；--same-asset 时两段同源，用来复现"同键并发裁剪"
    return [candidates[0] if SAME_ASSET else candidates[i % len(candidates)] for i in range(count)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V32：负时间片头音画同步")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--same-asset", action="store_true",
                        help="两段用同一素材（复现同键并发裁剪，验证文件占用修复）")
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=270)
    args = parser.parse_args(argv)
    global SAME_ASSET
    SAME_ASSET = bool(args.same_asset)

    from fastapi.testclient import TestClient

    from clipwright.main import app

    started = time.perf_counter()
    report: dict = {"ok": False, "preroll_sec": PREROLL_SEC, "song_sec": SONG_SEC}

    song = _make_song(ROOT / "_cache" / "v32" / "song.wav", SONG_SEC)
    assets = _pick_placeholder(2)
    report["song"] = str(song.relative_to(ROOT))
    report["assets"] = [str(a.relative_to(ROOT)) for a in assets]

    timeline = {
        "id": "tl_v32",
        "width": args.width,
        "height": args.height,
        "fps": 30,
        "duration_sec": SONG_SEC,
        "tracks": [{
            "id": "tv", "name": "视频轨", "kind": "video", "index": 0,
            "clips": [
                {"id": "v0", "kind": "video", "asset_id": str(assets[0].relative_to(ROOT)),
                 "track_id": "tv", "start_sec": 0.0, "duration_sec": SONG_SEC / 2},
                {"id": "v1", "kind": "video", "asset_id": str(assets[1].relative_to(ROOT)),
                 "track_id": "tv", "start_sec": SONG_SEC / 2, "duration_sec": SONG_SEC / 2},
            ],
        }],
    }

    with TestClient(app) as client:
        res = client.post("/api/render/queue", json={
            "timeline": timeline,
            "output_path": "v32_preroll.mp4",
            "audio_file_path": str(song.relative_to(ROOT)),
            "preroll_sec": PREROLL_SEC,
            "settings": {"width": args.width, "height": args.height, "fps": 30},
        })
        report["queue_http"] = res.status_code
        if res.status_code >= 400:
            report["error"] = res.text[:400]
            _write(report, started)
            return 1
        task_id = str((res.json() or {}).get("task_id") or "")
        report["task_id"] = task_id

        status = ""
        task: dict = {}
        deadline = time.perf_counter() + args.timeout
        while time.perf_counter() < deadline:
            task = client.get(f"/api/render/queue/{task_id}").json()
            status = str(task.get("status") or "")
            if status in TERMINAL:
                break
            time.sleep(args.interval)
        report["status"] = status
        report["task_preroll"] = task.get("preroll")
        result = task.get("result") or {}
        report["result_error"] = result.get("error")
        output = result.get("output_path") or ""

    if status != "completed" or not output:
        report["error"] = report.get("error") or f"渲染未完成: {status}"
        _write(report, started)
        return 1

    video_sec = _probe_seconds(Path(output))
    audio_sec = _probe_seconds(song)
    report["video_sec"] = video_sec
    report["audio_sec"] = audio_sec

    from tools.check_sync import check_sync

    sync = check_sync(video_sec, audio_sec, preroll_sec=PREROLL_SEC)
    report["sync"] = sync.as_dict()
    report["sync_summary"] = sync.summary()
    report["ok"] = sync.ok
    report["elapsed_sec"] = round(time.perf_counter() - started, 1)
    _write(report, started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write(report: dict, started: float) -> None:
    report.setdefault("elapsed_sec", round(time.perf_counter() - started, 1))
    target = ROOT / "out" / "v32" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
