"""成片文字动画核对（PRD text-animation-llm M3）：真的渲染一遍，再用像素说话。

用 ffmpeg 现场造一小段成片：纯色背景 + 一个带 keyframes 的 drawtext 文字 + 一条字幕，
然后断言：
1. **中心带有墨迹**（文字真的画在画面中央）；
2. **字幕带在只有文字窗口时是干净的**（文字没压到字幕带）；
3. **窗口内帧间运动量 > 阈值**（动画在动，不是冻在最后一帧——旧 setpts 缺失的症状）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.services import render_qa


pytestmark = pytest.mark.skipif(not render_qa.ffmpeg_available(), reason="需要 ffmpeg")


def _font() -> str:
    """用仓库里已有的字体（Windows 没装 fontconfig，drawtext 必须显式给 fontfile）。"""
    from clipwright.services.render import _resolve_system_font  # noqa: PLC0415

    return _resolve_system_font()


def _render(out: Path, *, with_text: bool, with_caption: bool) -> Path:
    """合成一小段测试片：深色底 + 可选中心文字（带位移/缩放动画）+ 可选底部字幕。"""
    dur = 3.0
    font = _font()
    chains = ["[0:v]null[base0]"]
    last = "base0"
    if with_text:
        # 文字从下往上滑入：0~0.5s 在动，之后停住（模拟入场动画）
        chains.append(
            f"[{last}]drawtext=fontfile={font}:text='MOTION CLIP'"
            ":fontcolor=white:fontsize=54"
            ":x=(w-text_w)/2:y=(h-text_h)/2+60-120*min(t/0.5\\,1)"
            ":alpha=min(t/0.3\\,1)[t1]")
        last = "t1"
    if with_caption:
        chains.append(
            f"[{last}]drawtext=fontfile={font}:text='caption line'"
            ":fontcolor=white:fontsize=28"
            ":x=(w-text_w)/2:y=h-th-16[c1]")
        last = "c1"
    chains.append(f"[{last}]null[out]")
    script = ";".join(chains)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x101418:s=640x360:r=30:d={dur}",
        "-filter_complex", script, "-map", "[out]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "ignore")[:400]
    return out


def test_text_drawn_at_center_and_moving(tmp_path: Path):
    video = _render(tmp_path / "with_text.mp4", with_text=True, with_caption=False)
    stats = render_qa.analyze_window(str(video), 0.0, 1.2)

    assert stats.frames >= 6, "取帧太少，无法判断运动"
    assert stats.ink["center"] > 0.01, f"中心带没有墨迹：{stats.ink}"
    assert stats.ink["caption"] < stats.ink["center"] / 3, (
        f"文字压到了字幕带：{stats.ink}")
    assert stats.motion > 1.0, f"文字动画没有运动（motion={stats.motion}）"


def test_static_render_has_no_motion(tmp_path: Path):
    """对照组：没有文字动画时窗口内几乎不动——阈值才站得住。"""
    video = _render(tmp_path / "static.mp4", with_text=False, with_caption=True)
    stats = render_qa.analyze_window(str(video), 0.0, 1.2)
    assert stats.ink["caption"] > 0.005, f"字幕带应该有字：{stats.ink}"
    assert stats.motion < 1.0, f"静止片不该有运动：motion={stats.motion}"


def test_cli_emits_json(tmp_path: Path, capsys):
    video = _render(tmp_path / "cli.mp4", with_text=True, with_caption=False)
    assert render_qa.main([str(video), "--window", "0,1", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body[0]["frames"] > 0 and "ink" in body[0]
