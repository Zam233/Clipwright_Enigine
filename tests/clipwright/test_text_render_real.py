"""文字动画**真的**渲染进成片了（PRD text-animation-llm M3）。

前面两组测试只证明"关键帧写对了"（`test_text_animation_wiring`）和"像素指标可分辨"
（`test_render_qa`）。这一组是闭环：拿一条最小的真实时间线跑一次真实 ffmpeg 渲染，
再把成片取帧做差——**文字是否真的出现在画面中央、是否真的在动、是否压到字幕带**。

为什么必须这么测（E2E 2026-10-30 实测）：管线里文字的 clip/keyframes 全对、drawtext
滤镜串也对，但成片帧里什么都没有。只有"渲染 + 取帧 + 前后对比"能抓住这类
"数据对了、画面没有"的故障。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services import render_qa
from clipwright.services.render import RenderService


pytestmark = pytest.mark.skipif(not render_qa.ffmpeg_available(), reason="需要 ffmpeg")


def _source_video(path: Path, *, seconds: float = 5.0) -> Path:
    """造一段安静的纯色底片（文字对比度才够，取帧统计不受画面内容干扰）。"""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"color=c=0x101418:s=640x360:r=30:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(path)],
        check=True, capture_output=True,
    )
    return path


def _timeline(src: Path, *, with_text: bool, with_caption: bool = False) -> Timeline:
    video = Track(id="t_v", kind=ClipKind.VIDEO, index=0, clips=[
        Clip(id="v1", kind=ClipKind.VIDEO, asset_id=str(src), track_id="t_v",
             start_sec=0.0, duration_sec=5.0),
    ])
    tracks = [video]
    if with_text:
        tracks.append(Track(id="t_text", kind=ClipKind.TEXT, index=1, clips=[
            Clip(id="tc1", kind=ClipKind.TEXT, asset_id="", track_id="t_text",
                 start_sec=1.0, duration_sec=3.0, text="声音是一整条链",
                 font_size=64, font_color="#ffffff", text_align="center",
                 font="sans-serif",
                 # 淡入 + 从下往上滑 40px（既有透明度也有位移，动画才算"真的在动"）
                 keyframes=[
                     {"time": 1.0, "properties": {"opacity": 0, "translate_y": 40}},
                     {"time": 1.4, "properties": {"opacity": 1, "translate_y": 0}},
                     {"time": 2.4, "properties": {"opacity": 1, "translate_y": 0}},
                     {"time": 3.6, "properties": {"opacity": 1, "translate_y": 0}},
                     {"time": 3.9, "properties": {"opacity": 0, "translate_y": 0}},
                 ],
                 metadata={"anim_type": "word_emphasis", "renderer": "drawtext",
                           "position": "center"}),
        ]))
    if with_caption:
        tracks.append(Track(id="t_cap", kind=ClipKind.CAPTION, index=2, clips=[
            Clip(id="c1", kind=ClipKind.CAPTION, asset_id="", track_id="t_cap",
                 start_sec=1.0, duration_sec=3.0, text="这是字幕",
                 font_size=32, font_color="#ffffff", text_align="center",
                 metadata={"category": "caption", "position": "bottom"}),
        ]))
    return Timeline(id="tl_text_real", width=640, height=360, fps=30.0,
                    duration_sec=5.0, tracks=tracks)


def _render(tmp_path: Path, timeline: Timeline, name: str) -> Path:
    out = tmp_path / name
    result = asyncio.run(RenderService(work_dir=tmp_path / f"wd_{name}").render(
        timeline, out, width=640, height=360, fps=30.0,
        encoder_override="libx264", preset_override="ultrafast",
    ))
    assert result.success, f"渲染失败：{result.error}"
    assert out.exists() and out.stat().st_size > 2_000  # 纯色底片很小，别用过高的门槛
    return out


def _frame(tmp_path: Path, video: Path, at: float, name: str) -> Path:
    path = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(video),
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )
    return path


def _diff_ratio(a: Path, b: Path) -> float:
    """两张同尺寸帧的差异像素占比（灰度取绝对差 > 18 视为不同）。"""
    def gray(p: Path) -> bytes:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(p), "-vf", "format=gray",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            check=True, capture_output=True,
        )
        return out.stdout

    pa, pb = gray(a), gray(b)
    if not pa or len(pa) != len(pb):
        raise AssertionError("帧尺寸不一致，无法比对")
    diff = sum(1 for x, y in zip(pa, pb) if abs(x - y) > 18)
    return diff / len(pa)


def test_text_overlay_burns_into_frames(tmp_path: Path):
    """有文字 vs 无文字：中间带明显不同、字幕带几乎不变。"""
    src = _source_video(tmp_path / "src.mp4")
    plain = _render(tmp_path, _timeline(src, with_text=False), "plain.mp4")
    withtext = _render(tmp_path, _timeline(src, with_text=True), "withtext.mp4")

    f_plain = _frame(tmp_path, plain, 2.5, "plain.png")
    f_text = _frame(tmp_path, withtext, 2.5, "text.png")

    whole = _diff_ratio(f_plain, f_text)
    assert whole > 0.01, f"成片里看不到文字（整帧差异只有 {whole:.2%}）"

    # 分带：中心带必须有差，字幕带必须几乎无差（文字没压字幕）
    ink_text = render_qa.analyze_window(str(withtext), 2.4, 0.6)
    ink_plain = render_qa.analyze_window(str(plain), 2.4, 0.6)
    center_gain = ink_text.ink["center"] - ink_plain.ink["center"]
    caption_gain = ink_text.ink["caption"] - ink_plain.ink["caption"]
    assert center_gain > 0.02, f"中心带没有新增墨迹（+{center_gain:.3f}）"
    assert caption_gain < center_gain / 2, (
        f"文字压到了字幕带：center+{center_gain:.3f} caption+{caption_gain:.3f}")


def test_text_animation_is_moving(tmp_path: Path):
    """动画窗口内逐帧差异明显大于静止窗口——不是静态贴图，也没冻在最后一帧。"""
    src = _source_video(tmp_path / "src2.mp4")
    video = _render(tmp_path, _timeline(src, with_text=True), "motion.mp4")

    animating = render_qa.analyze_window(str(video), 1.0, 0.5)   # 淡入 + 上滑
    still = render_qa.analyze_window(str(video), 3.0, 0.4)       # 已静止
    assert animating.motion > still.motion * 1.5, (
        f"入场动画没有明显运动：animating={animating.motion} still={still.motion}")


def test_caption_and_text_coexist_without_overlap(tmp_path: Path):
    """文字在中心、字幕在底部：两条都在，且互不侵占。"""
    src = _source_video(tmp_path / "src3.mp4")
    video = _render(tmp_path, _timeline(src, with_text=True, with_caption=True), "both.mp4")
    stats = render_qa.analyze_window(str(video), 2.4, 0.6)
    assert stats.ink["center"] > 0.02 and stats.ink["caption"] > 0.01, stats.ink
