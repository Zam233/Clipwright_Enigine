"""渲染期底板兜底（PRD §7.4 的落地实现）。

问题：MG 动画的透明层直接叠在实拍画面上，浅背景 + 白字时对比度只有 ~1.1:1，
人眼不可读。生成期已经做了「提示词要求 + 裸文本自动补投影」，但 LLM 仍可能
给出无底板、无投影的文本，或者背景恰好很亮。

这里做最后一公里：**在动画层下方合成一层只在文字附近出现的半透明黑底板**。
实现刻意避开复杂的 ffmpeg 滤镜图（`alphamerge` + `blend` + `lut` 在不同版本上
行为不一致，实测会把底板稀释成几乎不可见），改为：

1. 用 ffmpeg 把动画 MOV 解码成 RGBA 原始帧（管道，不落盘）；
2. 用 PIL 对 **alpha 通道**做形态学膨胀（`MaxFilter`）+ 高斯模糊 → 得到比文字
   大一圈的柔和遮罩；
3. 遮罩乘 `alpha` 强度，作为纯黑 RGBA 层的 alpha；
4. 写回 ffmpeg 编码成带 alpha 的 MOV（qtrle）。

是否启用由**背景亮度采样**决定（`sample_brightness`）：只有偏亮的画面才加底板，
深色画面保持原样，避免无谓压暗。
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Iterable

from clipwright.config import logger

#: 默认参数（可被 settings 覆盖）
DEFAULT_SCRIM_ALPHA = 0.62
DEFAULT_SCRIM_GROW = 10          # 膨胀像素（底板比文字大一圈）
DEFAULT_SCRIM_BLUR = 6.0         # 柔和边缘
DEFAULT_LIGHT_THRESHOLD = 140    # 背景平均亮度（0-255）超过它就加底板


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def probe_frame(path: str) -> tuple[int, int, float, float]:
    """返回 (宽, 高, 帧率, 时长)。"""
    r = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json", path,
    ], text=True)
    try:
        data = json.loads(r.stdout or "{}")
        st = (data.get("streams") or [{}])[0]
        num, _, den = str(st.get("r_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        return int(st.get("width") or 0), int(st.get("height") or 0), fps, float(
            (data.get("format") or {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        return 0, 0, 30.0, 0.0


def sample_brightness(video: str, at_sec: float) -> float:
    """抽一帧算平均亮度（0-255）。失败返回 -1（调用方据此跳过底板）。"""
    png = None
    try:
        import tempfile

        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            png = fh.name
        r = _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at_sec):.3f}",
                  "-i", video, "-frames:v", "1", png])
        if r.returncode != 0:
            return -1.0
        img = Image.open(png).convert("L")
        hist = img.histogram()
        total = sum(hist) or 1
        return sum(i * c for i, c in enumerate(hist)) / total
    except Exception:  # noqa: BLE001
        return -1.0
    finally:
        if png:
            Path(png).unlink(missing_ok=True)


def scrim_frame(rgba, alpha: float = DEFAULT_SCRIM_ALPHA, grow: int = DEFAULT_SCRIM_GROW,
                blur: float = DEFAULT_SCRIM_BLUR):
    """把一帧动画（RGBA）转成「纯黑 + 柔和遮罩 alpha」的底板帧。"""
    from PIL import Image, ImageFilter

    grow = max(0, int(grow))
    a = rgba.getchannel("A")
    factor = max(0.0, min(1.0, float(alpha)))

    bbox = a.getbbox()
    if not bbox:
        return Image.new("RGBA", rgba.size, (0, 0, 0, 0))

    # 视频里通行做法是「规整的文字条/卡片」而不是沿笔画膨胀几像素：
    # 前者能真正把文字与背景隔开，后者只是给笔画描一圈边（实测对比度几乎不变）。
    # 用 alpha 的包围盒 + padding 画圆角矩形，再做高斯模糊柔化边缘。
    from PIL import ImageDraw

    pad_x = int(max(12, grow * 3))
    pad_y = int(max(10, grow * 2))
    left = max(0, bbox[0] - pad_x)
    top = max(0, bbox[1] - pad_y)
    right = min(rgba.size[0], bbox[2] + pad_x)
    bottom = min(rgba.size[1], bbox[3] + pad_y)

    mask = Image.new("L", rgba.size, 0)
    draw = ImageDraw.Draw(mask)
    radius = max(6, min(28, (bottom - top) // 3))
    draw.rounded_rectangle(
        [left, top, right, bottom], radius=radius, fill=int(round(factor * 255)),
    )
    if blur and blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(blur)))

    scrim = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    scrim.putalpha(mask)
    return scrim


def build_scrim_mov(
    src_mov: str, out_mov: str, *,
    alpha: float = DEFAULT_SCRIM_ALPHA,
    grow: int = DEFAULT_SCRIM_GROW,
    blur: float = DEFAULT_SCRIM_BLUR,
    timeout: float = 600.0,
) -> str | None:
    """用动画 MOV 的 alpha 生成同尺寸/同帧率/同时长的底板 MOV。"""
    from PIL import Image

    width, height, fps, duration = probe_frame(src_mov)
    if width <= 0 or height <= 0:
        logger.warning("Scrim: 无法读取 %s 尺寸，跳过底板", src_mov)
        return None
    frame_bytes = width * height * 4
    out = Path(out_mov)
    out.parent.mkdir(parents=True, exist_ok=True)

    decode = ["ffmpeg", "-v", "error", "-i", src_mov, "-f", "rawvideo",
              "-pix_fmt", "rgba", "-"]
    encode = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
        "-r", f"{fps:.6f}", "-i", "-",
        "-c:v", "qtrle", "-pix_fmt", "argb", str(out),
    ]
    try:
        dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert dec.stdout is not None and enc.stdin is not None
        frames = 0
        while True:
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            img = Image.frombytes("RGBA", (width, height), buf)
            enc.stdin.write(scrim_frame(img, alpha=alpha, grow=grow, blur=blur).tobytes())
            frames += 1
            if frames > 20000:  # 防御：异常输入不至于无限循环
                break
        dec.stdout.close()
        dec.wait(timeout=timeout)
        enc.stdin.close()
        enc.wait(timeout=timeout)
        if frames == 0 or enc.returncode != 0 or not out.exists():
            logger.warning("Scrim: 底板 MOV 生成失败（frames=%d rc=%s）", frames, enc.returncode)
            return None
        logger.info("Scrim: 生成底板 %s（%d 帧，alpha=%.2f grow=%d）", out.name, frames, alpha, grow)
        return str(out)
    except Exception as e:  # noqa: BLE001
        logger.warning("Scrim: 底板生成异常: %s", e)
        return None


def settings_of() -> dict[str, float | int | bool]:
    """从 settings 取底板参数（缺失时用本模块默认）。"""
    try:
        from clipwright.config import settings

        return {
            "enabled": bool(getattr(settings, "animation_render_scrim", True)),
            "alpha": float(getattr(settings, "animation_scrim_alpha", DEFAULT_SCRIM_ALPHA)),
            "grow": int(getattr(settings, "animation_scrim_grow", DEFAULT_SCRIM_GROW)),
            "blur": float(getattr(settings, "animation_scrim_blur", DEFAULT_SCRIM_BLUR)),
            "light_threshold": float(
                getattr(settings, "animation_scrim_light_threshold", DEFAULT_LIGHT_THRESHOLD)),
        }
    except Exception:  # noqa: BLE001
        return {"enabled": True, "alpha": DEFAULT_SCRIM_ALPHA, "grow": DEFAULT_SCRIM_GROW,
                "blur": DEFAULT_SCRIM_BLUR, "light_threshold": DEFAULT_LIGHT_THRESHOLD}


def should_scrim(video: str, at_sec: float, light_threshold: float | None = None) -> bool:
    """背景亮度是否高到需要底板（采样失败时保守返回 False）。"""
    cfg = settings_of()
    threshold = float(cfg["light_threshold"] if light_threshold is None else light_threshold)
    lum = sample_brightness(video, at_sec)
    if lum < 0:
        return False
    return lum >= threshold


def bake_scrim_into_mov(
    src_mov: str, out_mov: str, *,
    alpha: float = DEFAULT_SCRIM_ALPHA,
    grow: int = DEFAULT_SCRIM_GROW,
    blur: float = DEFAULT_SCRIM_BLUR,
    timeout: float = 600.0,
) -> str | None:
    """把底板**烧进**动画 MOV（Python 侧逐帧 alpha 合成）。

    为什么不在 ffmpeg 里叠两层 overlay：实测「scrim 层 + 动画层」两次 overlay 时
    第一层会被吞掉（背景亮度回到未加底板的状态）。逐帧用 PIL 合成只依赖一条
    编解码管道，行为确定、可单测，渲染链路完全不用改滤镜图。
    """
    from PIL import Image

    width, height, fps, _duration = probe_frame(src_mov)
    if width <= 0 or height <= 0:
        logger.warning("Scrim: 无法读取 %s 尺寸，跳过烘焙", src_mov)
        return None
    frame_bytes = width * height * 4
    out = Path(out_mov)
    out.parent.mkdir(parents=True, exist_ok=True)
    decode = ["ffmpeg", "-v", "error", "-i", src_mov, "-f", "rawvideo",
              "-pix_fmt", "rgba", "-"]
    encode = ["ffmpeg", "-y", "-loglevel", "error",
              "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
              "-r", f"{fps:.6f}", "-i", "-",
              "-c:v", "qtrle", "-pix_fmt", "argb", str(out)]
    try:
        dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert dec.stdout is not None and enc.stdin is not None
        frames = 0
        while True:
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            img = Image.frombytes("RGBA", (width, height), buf)
            # 顺序很重要：底板在下、动画在上（alpha_composite 是把参数叠到 self 上）
            merged = scrim_frame(img, alpha=alpha, grow=grow, blur=blur)
            merged.alpha_composite(img)
            enc.stdin.write(merged.tobytes())
            frames += 1
            if frames > 20000:
                break
        dec.stdout.close()
        dec.wait(timeout=timeout)
        enc.stdin.close()
        enc.wait(timeout=timeout)
        if frames == 0 or enc.returncode != 0 or not out.exists():
            logger.warning("Scrim: 烘焙失败（frames=%d rc=%s）", frames, enc.returncode)
            return None
        logger.info("Scrim: 已烘焙底板 %s（%d 帧 alpha=%.2f）", out.name, frames, alpha)
        return str(out)
    except Exception as e:  # noqa: BLE001
        logger.warning("Scrim: 烘焙异常: %s", e)
        return None


__all__ = [
    "DEFAULT_LIGHT_THRESHOLD",
    "DEFAULT_SCRIM_ALPHA",
    "DEFAULT_SCRIM_BLUR",
    "DEFAULT_SCRIM_GROW",
    "bake_scrim_into_mov",
    "build_scrim_mov",
    "probe_frame",
    "sample_brightness",
    "scrim_frame",
    "settings_of",
    "should_scrim",
]
