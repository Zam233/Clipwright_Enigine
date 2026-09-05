"""真实渲染端到端冒烟 — fix-report-2「回归验收」节的可执行版本。

不依赖 LLM/MongoDB：合成源素材（ffmpeg testsrc2/sine），直接构造时间线，
驱动 RenderService 全管线（trim 并行 → xfade 转场 → 字幕 → 链式 MG → PIP → 混音），
随后对产物做 ffprobe/抽帧校验。

用法（工作目录 J:\\Clipwright）：
    python scripts/e2e_smoke.py            # 全量（含 MG，需 Chromium）
    python scripts/e2e_smoke.py --no-mg    # 跳过 MG 片段

退出码 0 = 全部断言通过。所有子进程调用均为参数列表形式（shell=False）。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def run_ffmpeg(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """参数列表执行 ffmpeg（固定可执行名，无 shell）。"""
    return subprocess.run([FFMPEG, *args], capture_output=True, text=True,
                          timeout=timeout, shell=False)


def run_ffprobe_json(path: Path) -> dict:
    """参数列表执行 ffprobe 并解析 JSON。"""
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=60, shell=False)
    assert r.returncode == 0, r.stderr[-300:]
    return json.loads(r.stdout)


def synth_sources(work: Path) -> dict[str, Path]:
    """合成 3 个主视频 + 1 个 PIP 视频 + 1 段 BGM（全部含可辨识内容）。"""
    src: dict[str, Path] = {}
    for i in (1, 2, 3):
        p = work / f"main{i}.mp4"
        freq = 300 + i * 100
        r = run_ffmpeg(["-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=6",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)])
        assert r.returncode == 0, r.stderr[-400:]
        src[f"main{i}"] = p
    pip = work / "pip.mp4"
    r = run_ffmpeg(["-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:duration=5",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(pip)])
    assert r.returncode == 0, r.stderr[-400:]
    src["pip"] = pip
    bgm = work / "bgm.m4a"
    r = run_ffmpeg(["-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=14",
                    "-c:a", "aac", str(bgm)])
    assert r.returncode == 0, r.stderr[-400:]
    src["bgm"] = bgm
    return src


def build_mg_html() -> tuple[str, dict]:
    """用内置模板产一段真实 MG HTML（与管线同源）。"""
    from clipwright.animation.mg_renderer import MGRenderer
    mg_def = MGRenderer.load_animation("mg_quote_card")
    if mg_def is None:
        # 任一回退到目录里第一个模板
        tpl_dir = Path(__file__).resolve().parent.parent / "clipwright" / "animation" / "mg" / "templates"
        first = sorted(tpl_dir.glob("*.json"))[0]
        mg_def = json.loads(first.read_text(encoding="utf-8"))
    params = {}
    for k, v in (mg_def.get("params") or {}).items():
        params[k] = v.get("default", "") if isinstance(v, dict) else str(v)
    html = MGRenderer.render(mg_def, params, width=1280, height=720, fps=30)
    return html, mg_def


def build_timeline(src: dict[str, Path], with_mg: bool) -> dict:
    tracks = [
        {"id": "t0", "name": "V1", "kind": "video", "index": 0,
         "locked": False, "muted": False, "clips": [
            {"id": "c1", "kind": "video", "asset_id": str(src["main1"]), "track_id": "t0",
             "start_sec": 0.0, "duration_sec": 5.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0.8, "opacity": 1, "keyframes": [], "metadata": {}},
            {"id": "c2", "kind": "video", "asset_id": str(src["main2"]), "track_id": "t0",
             "start_sec": 5.0, "duration_sec": 5.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0.8, "opacity": 1, "keyframes": [],
             "transition_in": "wipeleft", "transition_duration_sec": 0.5,
             "metadata": {"transform": {"x": 0.05, "scale": 1.1}}},
            {"id": "c3", "kind": "video", "asset_id": str(src["main3"]), "track_id": "t0",
             "start_sec": 10.0, "duration_sec": 5.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0.8, "opacity": 1,
             "transition_in": "fade", "transition_duration_sec": 0.5,
             "keyframes": [
                 {"time": 0, "properties": {"opacity": 0}},
                 {"time": 1, "properties": {"opacity": 1}, "easing": "ease-out-cubic"},
             ],
             "metadata": {"kf_time_base": "clip_local"}},
         ]},
        # PIP 轨（index>0 = 叠加层）
        {"id": "t1", "name": "PIP", "kind": "video", "index": 1,
         "locked": False, "muted": False, "clips": [
            {"id": "p1", "kind": "video", "asset_id": str(src["pip"]), "track_id": "t1",
             "start_sec": 2.0, "duration_sec": 4.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
             "image_rect": {"x": 0.62, "y": 0.08, "w": 0.3, "h": 0.25}},
         ]},
        # 字幕轨
        {"id": "t2", "name": "字幕", "kind": "caption", "index": 2,
         "locked": False, "muted": False, "clips": [
            {"id": "s1", "kind": "caption", "asset_id": "", "track_id": "t2",
             "start_sec": 0.5, "duration_sec": 3.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
             "text": "第一条字幕：帧艺端到端冒烟", "font_size": 48,
             "stroke_width": 2, "stroke_color": "#000000"},
            {"id": "s2", "kind": "caption", "asset_id": "", "track_id": "t2",
             "start_sec": 6.0, "duration_sec": 3.5, "source_offset_sec": 0, "speed": 1,
             "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
             "text": "第二条字幕：转场后画面继续", "font_size": 48,
             "stroke_width": 2, "stroke_color": "#000000"},
         ]},
    ]
    if with_mg:
        html, mg_def = build_mg_html()
        assert html, "MGRenderer.render 未产出 HTML"
        tracks.append({
            "id": "t3", "name": "MG", "kind": "animation", "index": 3,
            "locked": False, "muted": False, "clips": [
                {"id": "m1", "kind": "animation", "asset_id": "", "track_id": "t3",
                 "start_sec": 7.0, "duration_sec": 4.0, "source_offset_sec": 0, "speed": 1,
                 "volume": 0, "opacity": 1, "keyframes": [],
                 "text": "冒烟|MG 回看",
                 "metadata": {"category": "mg", "renderer": "mg_hyperframes",
                              "mg_html": html, "mg_def": mg_def,
                              "mg_generation_id": "gen_smoketest",
                              "position": "center"}},
            ]})
    # 音频轨：BGM（角色标记）+ 一段变速人声占位（复用主视频音源）
    tracks.append({
        "id": "t4", "name": "音频", "kind": "audio", "index": 4,
        "locked": False, "muted": False, "clips": [
            {"id": "a1", "kind": "audio", "asset_id": str(src["bgm"]), "track_id": "t4",
             "start_sec": 0.0, "duration_sec": 14.0, "source_offset_sec": 0, "speed": 1,
             "volume": 0.3, "opacity": 1, "keyframes": [],
             "metadata": {"bgm": True}, "audio_fade_in_sec": 1.0, "audio_fade_out_sec": 1.0},
            {"id": "a2", "kind": "audio", "asset_id": str(src["main1"]), "track_id": "t4",
             "start_sec": 5.0, "duration_sec": 4.0, "source_offset_sec": 0.5, "speed": 1.5,
             "volume": 0.9, "opacity": 1, "keyframes": [],
             "metadata": {"narration": True}},
        ]})
    return {"id": "smoke", "width": 1280, "height": 720, "fps": 30, "duration_sec": 15,
            "tracks": tracks, "markers": []}


def probe_meta(path: Path) -> dict:
    data = run_ffprobe_json(path)
    streams = data.get("streams", [])
    return {
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "duration": float(data.get("format", {}).get("duration", 0)),
    }


def frame_luma(path: Path, t: float) -> float:
    """取 t 时刻帧的平均亮度（signalstats YAVG）。"""
    r = run_ffmpeg(["-ss", str(t), "-i", str(path), "-frames:v", "1",
                    "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                    "-f", "null", "-"], timeout=60)
    import re
    m = re.search(r"YAVG=([\d.]+)", r.stderr or "")
    return float(m.group(1)) if m else -1.0


async def main() -> int:
    with_mg = "--no-mg" not in sys.argv
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cw_smoke_") as tmp:
        work = Path(tmp)
        print("[1/4] 合成源素材…")
        src = synth_sources(work)

        print("[2/4] 构造时间线…")
        tl = build_timeline(src, with_mg)
        out = work / "smoke_out.mp4"

        print("[3/4] 全管线渲染（转场×2 + 字幕×2 + PIP + 音频×2"
              + (" + MG×1" if with_mg else "") + "）…")
        from clipwright.schema.timeline import Timeline
        from clipwright.services.render import RenderService

        async def progress(stage, pct, msg):
            print(f"    [{stage:>8}] {pct:5.1f}% {msg}")

        rs = RenderService(work_dir=work / "engine")
        result = await rs.render(Timeline(**tl), out,
                                 width=1280, height=720, fps=30,
                                 progress_callback=progress)
        if not result.success:
            print("渲染失败:", result.error)
            print("\n".join(result.ffmpeg_log.splitlines()[-25:]))
            return 1

        print("[4/4] 产物校验…")
        meta = probe_meta(out)
        print(f"    duration={meta['duration']:.2f}s video={meta['has_video']} audio={meta['has_audio']}")

        if not meta["has_video"]:
            failures.append("缺少视频流")
        if not meta["has_audio"]:
            failures.append("缺少音频流")
        # 3×5s 背靠背 − 2×0.5s xfade 重叠 ≈ 14s
        if not 13.0 <= meta["duration"] <= 15.5:
            failures.append(f"成片时长 {meta['duration']:.2f}s 偏离预期 [13,15.5]")
        if rs._fallback_count > 0:
            failures.append(f"发生 {rs._fallback_count} 次源降级（fallback plate）")

        # 转场后帧非黑（c3 起点附近，opacity 关键帧渐入中段）
        luma_mid = frame_luma(out, 12.0)
        if luma_mid < 20:
            failures.append(f"t=12s 帧平均亮度 {luma_mid:.1f} 过低（疑似黑屏）")
        # 字幕时段帧非黑
        luma_cap = frame_luma(out, 2.0)
        if luma_cap < 20:
            failures.append(f"t=2s 帧平均亮度 {luma_cap:.1f} 过低")

        print(f"    luma(t=2s)={luma_cap:.1f} luma(t=12s)={luma_mid:.1f} fallback={rs._fallback_count}")

    if failures:
        print("\nSMOKE FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nSMOKE PASS ✓")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
