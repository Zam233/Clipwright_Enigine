"""P8: 参考成片风格模仿 — 分析参考视频，提取风格参数（节奏/配色/转场），
写入 persona 参数层（ParameterLayer.embedding: RhythmStats/VisualStats）。

方法：
- 节奏：ffmpeg scene 检测 → 镜头时长分布（mu/sigma/variance）
- 配色：抽帧 → PIL 降采样取主色簇（RGB）
- 转场：scene 检测窗口内变化次数 → transition_weights 粗估
任何步骤失败都返回部分结果（零依赖容错），不阻断 persona 更新。
"""

from __future__ import annotations

import subprocess
import re
from typing import Any

from clipwright.config import logger


async def analyze_reference_video(video_path: str) -> dict[str, Any]:
    """分析参考成片，返回风格参数。"""
    result: dict[str, Any] = {"source": video_path}

    # 1. 节奏：scene detect 输出镜头边界时间
    shot_durations: list[float] = []
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf",
             "select='gt(scene,0.35)',metadata=print:key=lavfi.scene_score",
             "-frames:v", "600", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        pts = re.findall(r"pts_time:([\d.]+)", r.stderr)
        times = [float(t) for t in pts]
        if len(times) >= 2:
            shot_durations = [round(times[i + 1] - times[i], 3) for i in range(len(times) - 1)]
    except Exception as e:
        logger.info("参考视频节奏分析失败（非致命）: %s", e)

    if shot_durations:
        import statistics
        mu = statistics.mean(shot_durations) * 1000  # ms
        sigma = statistics.stdev(shot_durations) * 1000 if len(shot_durations) > 1 else mu * 0.3
        result["rhythm"] = {
            "shot_duration_distribution": "log_normal",
            "shot_duration_mu_ms": round(mu, 1),
            "shot_duration_sigma_ms": round(sigma, 1),
            "pacing_variance_per_minute": round(min(1.0, sigma / max(mu, 1) * 0.5), 2),
            "shot_count": len(shot_durations),
        }

    # 2. 配色：抽 8 帧 → 平均主色簇
    try:
        colors = await _sample_dominant_colors(video_path)
        if colors:
            result["visual"] = {
                "dominant_color_cluster": colors,
                "saturation_median": 0.4,
                "contrast_median": 0.7,
                "motion_magnitude_median": 0.25,
            }
    except Exception as e:
        logger.info("参考视频配色分析失败（非致命）: %s", e)

    # 3. 转场强度粗估（镜头密度 → transition_weights）
    if shot_durations:
        density = len(shot_durations) / max(sum(shot_durations), 1.0)
        if density > 0.5:
            result["transition_weights"] = {"fade": 0.1, "dissolve": 0.3, "hard_cut": 0.5, "slide": 0.1}
        else:
            result["transition_weights"] = {"fade": 0.2, "dissolve": 0.4, "hard_cut": 0.3, "slide": 0.1}

    return result


async def _sample_dominant_colors(video_path: str, frames: int = 8) -> list[list[int]]:
    """抽帧 + 简单降采样取主色（无 PIL 时回退 ffmpeg scale 平均色）。"""
    import tempfile, os
    colors: list[list[int]] = []
    tmpdir = tempfile.mkdtemp(prefix="cw_style_")
    try:
        # 每 N 秒抽一帧 → 1x1 缩放取平均色（RGB）
        r = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf", "fps=1/4,scale=1:1,format=rgb24",
             "-frames:v", str(frames), f"{tmpdir}/f%02d.bmp"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return colors
        for f in sorted(os.listdir(tmpdir)):
            if not f.endswith(".bmp"):
                continue
            path = os.path.join(tmpdir, f)
            try:
                from PIL import Image  # 可选依赖
                img = Image.open(path).convert("RGB")
                px = img.getpixel((0, 0))
                colors.append([int(px[0]), int(px[1]), int(px[2])])
            except Exception:
                # 无 PIL：直接从 BMP 头解析（24-bit 底部像素）
                with open(path, "rb") as fp:
                    data = fp.read()
                if len(data) >= 54:
                    b, g, r_ = data[54], data[55], data[56]
                    colors.append([int(r_), int(g_), int(b)])
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    # 去重 + 截断
    dedup: list[list[int]] = []
    for c in colors:
        if all(abs(c[i] - d[i]) > 12 for d in dedup for i in range(3)):
            dedup.append(c)
        if len(dedup) >= 4:
            break
    return dedup
