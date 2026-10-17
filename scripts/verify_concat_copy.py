"""验收 V1 规则 2：无转场时 `-c copy` 无损拼接 vs 旧 filter 重编码。

同一条 3 片段、无转场的短时间线渲染两次（`CLIPWRIGHT_CONCAT_COPY` = 1 / 0），对比：
- 拼接阶段是否真的走了 `-c copy`（命令录制器取证）；
- 编码调用次数（copy 路径应少一次全片重编码）；
- 成片时长是否一致；
- 两条成片之间的 PSNR（copy 路径少一代编码，应不差于重编码路径）。

用法（工作目录 J:\\Clipwright，需 ffmpeg）：
    python scripts/verify_concat_copy.py
退出码 0 = 全部断言通过。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_smoke import probe_meta  # noqa: E402

FFMPEG = "ffmpeg"


def synth(work: Path, name: str, freq: int, dur: float = 2.0) -> Path:
    p = work / name
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]
    return p


def build_timeline(srcs: list[Path]) -> dict:
    tracks = [{
        "id": "v0", "name": "视频", "kind": "video", "index": 0, "clips": [
            {"id": f"c{i}", "kind": "video", "asset_id": str(p), "track_id": "v0",
             "start_sec": i * 2.0, "duration_sec": 2.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0, "opacity": 1, "keyframes": [], "metadata": {}}
            for i, p in enumerate(srcs)
        ],
    }, {
        # 音频轨：让混音阶段真实运行（否则产物无音轨）
        "id": "a0", "name": "音频", "kind": "audio", "index": 1, "clips": [
            {"id": "a1", "kind": "audio", "asset_id": str(srcs[0]), "track_id": "a0",
             "start_sec": 0.0, "duration_sec": 2.0 * len(srcs), "source_offset_sec": 0,
             "speed": 1, "volume": 0.5, "opacity": 1, "keyframes": [],
             "metadata": {"bgm": True}},
        ],
    }]
    return {"id": "concat_verify", "width": 640, "height": 360, "fps": 30,
            "duration_sec": 2.0 * len(srcs), "tracks": tracks, "markers": []}


def clear_caches() -> None:
    """清空视频阶段缓存与 trim 缓存：否则第二路渲染直接命中缓存，A/B 不成立。"""
    import clipwright.services.render as R

    try:
        for f in R._VIDEO_CACHE_DIR.glob("stage_*.mp4"):
            f.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        with R._trim_cache_lock:
            R._trim_cache.clear()
    except Exception:
        pass


_SINK: list[list[str]] = []
_RECORDER_INSTALLED = False


def install_recorder() -> list[list[str]]:
    """安装一次命令录制器（幂等：重复调用不会叠加包装，否则两次渲染的命令会串台）。

    命令写入模块级 `_SINK`；每次渲染前清空、渲染后取快照。
    """
    global _RECORDER_INSTALLED
    import clipwright.services.render as R

    if _RECORDER_INSTALLED:
        return _SINK
    orig_ff, orig_run_ff, orig_tracked = R.RenderService._ff, R.RenderService._run_ff, R.run_tracked_ff

    async def rec_ff(self, cmd, **kw):
        _SINK.append([str(c) for c in cmd]); return await orig_ff(self, cmd, **kw)

    def rec_run_ff(self, cmd, **kw):
        _SINK.append([str(c) for c in cmd]); return orig_run_ff(self, cmd, **kw)

    def rec_tracked(cmd, **kw):
        _SINK.append([str(c) for c in cmd]); return orig_tracked(cmd, **kw)

    R.RenderService._ff = rec_ff  # type: ignore[assignment]
    R.RenderService._run_ff = rec_run_ff  # type: ignore[assignment]
    R.run_tracked_ff = rec_tracked  # type: ignore[assignment]
    _RECORDER_INSTALLED = True
    return _SINK


def psnr(a: Path, b: Path) -> float | None:
    r = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "info", "-i", str(a), "-i", str(b),
         "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"average:([0-9.]+|inf)", r.stderr)
    if not m:
        return None
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


async def render_once(tl: dict, out: Path, work: Path, copy_mode: str) -> tuple[float, list[list[str]]]:
    import time

    from clipwright.schema.timeline import Timeline
    from clipwright.services.render import RenderService

    os.environ["CLIPWRIGHT_CONCAT_COPY"] = copy_mode
    clear_caches()
    sink = install_recorder()
    sink.clear()
    svc = RenderService(work_dir=work / f"engine_{copy_mode}")
    t0 = time.time()
    res = await svc.render(Timeline(**tl), out, width=640, height=360, fps=30)
    dt = time.time() - t0
    assert res.success, f"渲染失败(mode={copy_mode}): {res.error} | {res.ffmpeg_log[-300:]}"
    return dt, [list(c) for c in sink]


async def main() -> int:
    failures: list[str] = []
    work = Path(tempfile.mkdtemp(prefix="concat_verify_"))
    try:
        print("[1/3] 合成 3 个源片段…")
        srcs = [synth(work, f"m{i}.mp4", 300 + i * 100) for i in (1, 2, 3)]
        tl = build_timeline(srcs)

        print("[2/3] 预热 + 渲染两次（copy=1 / copy=0）…")
        out_copy, out_re = work / "copy.mp4", work / "reencode.mp4"
        # 先预热：把 trim 缓存（磁盘层 + 内存 LRU）填好，避免"第二路白嫖第一路的裁剪"
        await render_once(tl, work / "warmup.mp4", work, "1")
        dt_copy, rec_copy = await render_once(tl, out_copy, work, "1")
        dt_re, rec_re = await render_once(tl, out_re, work, "0")

        def count_encode(cmds: list[list[str]]) -> int:
            return sum(1 for c in cmds if "-c:v" in c or "-b:v" in c)

        def has_copy_concat(cmds: list[list[str]]) -> bool:
            return any("-f" in c and "concat" in c and "copy" in c for c in cmds)

        def filter_concat_cmds(cmds: list[list[str]]) -> list[list[str]]:
            return [c for c in cmds if "concat=n=" in " ".join(c)]

        def has_filter_concat(cmds: list[list[str]]) -> bool:
            return bool(filter_concat_cmds(cmds))

        print("[3/3] 取证…")
        meta_copy, meta_re = probe_meta(out_copy), probe_meta(out_re)
        enc_copy, enc_re = count_encode(rec_copy), count_encode(rec_re)
        ps = psnr(out_copy, out_re)
        print(f"    copy 路径: 时长={meta_copy['duration']:.2f}s 编码调用={enc_copy} "
              f"耗时={dt_copy:.1f}s copy-concat={has_copy_concat(rec_copy)} "
              f"filter-concat={has_filter_concat(rec_copy)}")
        print(f"    filter 路径: 时长={meta_re['duration']:.2f}s 编码调用={enc_re} "
              f"耗时={dt_re:.1f}s copy-concat={has_copy_concat(rec_re)} "
              f"filter-concat={has_filter_concat(rec_re)}")
        print(f"    两路 PSNR={ps}")
        for label, cmds in (("copy", rec_copy), ("filter", rec_re)):
            for c in filter_concat_cmds(cmds)[:2]:
                print(f"    [{label}] filter-concat: {' '.join(c)[:180]}")
            for c in cmds:
                if has_copy_concat([c]):
                    print(f"    [{label}] copy-concat: {' '.join(c)[:180]}")

        if not has_copy_concat(rec_copy):
            failures.append("copy=1 时未使用 concat demuxer + -c copy")
        if has_filter_concat(rec_copy):
            failures.append("copy=1 时仍在拼接阶段走了 filter 重编码")
        if has_copy_concat(rec_re):
            failures.append("copy=0 时仍走了 copy 路径（开关失效）")
        if not has_filter_concat(rec_re):
            failures.append("copy=0 时未走 filter 拼接（回退路径异常）")
        if abs(meta_copy["duration"] - meta_re["duration"]) > 0.2:
            failures.append(
                f"两路时长差异过大: {meta_copy['duration']:.2f} vs {meta_re['duration']:.2f}")
        for meta, label in ((meta_copy, "copy"), (meta_re, "filter")):
            if not (meta["has_video"] and meta["has_audio"]):
                failures.append(f"{label} 路径产物缺少视频/音频流: {meta}")
        if ps is not None and ps < 35:
            failures.append(f"copy 与重编码路径差异过大（PSNR={ps}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print("\nVERIFY FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nVERIFY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
