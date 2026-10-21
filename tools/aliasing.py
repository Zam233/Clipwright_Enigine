"""抗锯齿（锯齿）度量：4K 单帧"有没有混叠"的可复核判据（V34）。

两个纯函数：

- :func:`edge_metrics` —— 边缘带内的中间调占比与斜边台阶率（看"边缘有没有灰阶过渡"）；
- :func:`edge_band_mae` —— 两图在边缘带上的平均绝对差（看"和超采样参考差多少"）。

判据用**相对基准**而不是拍脑袋的绝对值：同一场景渲一版 2 倍超采样再 BOX 缩回，
作为抗锯齿的近似真值；再拿"最近邻放大"当劣质对照。若原生帧与参考的差只有劣质
对照的一小部分，就说明原生不是"硬边放大"，而是真的抗锯齿了。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def load_gray(path: str | Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def edge_band(gray: np.ndarray, threshold: float = 24.0, dilate: int = 2) -> np.ndarray:
    """强梯度点向四周膨胀 ``dilate`` 像素，得到"边缘带"掩码。"""
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    gh = min(gx.shape[0], gy.shape[0])
    gw = min(gx.shape[1], gy.shape[1])
    grad = gx[:gh, :gw] + gy[:gh, :gw]
    band = grad > threshold
    for _ in range(dilate):
        shifted = band.copy()
        shifted[1:, :] |= band[:-1, :]
        shifted[:-1, :] |= band[1:, :]
        shifted[:, 1:] |= band[:, :-1]
        shifted[:, :-1] |= band[:, 1:]
        band = shifted
    return band


def edge_metrics(gray: np.ndarray, *, mid_low: float = 24.0, mid_high: float = 232.0) -> dict:
    """边缘带的中间调占比 + 斜边台阶率。

    - ``mid_tone_ratio``：边缘带里落在 (mid_low, mid_high) 的像素比例。硬边几乎没有中间调，
      抗锯齿边缘会有连续灰阶；
    - ``stair_ratio``：逐行取最强梯度列，相邻采样行位置完全相同的比例。台阶（锯齿）会让
      这个值升高。
    """
    band = edge_band(gray)
    gh, gw = band.shape
    band_pixels = int(band.sum())
    mid = (gray[:gh, :gw] > mid_low) & (gray[:gh, :gw] < mid_high)
    mid_pixels = int((mid & band).sum())

    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    grad = np.zeros((gh, gw), dtype=np.float32)
    grad += gx[:gh, :gw]
    grad += gy[:gh, :gw]
    rows = list(range(int(gh * 0.15), int(gh * 0.85), 4))
    positions = [
        int(np.argmax(grad[row])) for row in rows
        if grad[row].size and float(grad[row].max()) > 24.0
    ]
    stairs = sum(1 for a, b in zip(positions, positions[1:]) if a == b)
    return {
        "edge_band_pixels": band_pixels,
        "mid_tone_ratio": round(mid_pixels / band_pixels, 4) if band_pixels else 0.0,
        "stair_ratio": round(stairs / (len(positions) - 1), 4) if len(positions) > 1 else 0.0,
        "sampled_rows": len(positions),
    }


def edge_band_mae(a_png: str | Path, b_png: str | Path, band_png: str | Path) -> dict:
    """两张同尺寸图在"边缘带"上的平均绝对差（灰度级 0–255）。"""
    a = load_gray(a_png)
    b = load_gray(b_png)
    band = edge_band(load_gray(band_png))
    gh, gw = band.shape
    diff = np.abs(a[:gh, :gw] - b[:gh, :gw])
    band_pixels = int(band.sum())
    return {
        "mean_abs_diff_all": round(float(diff.mean()), 3),
        "mean_abs_diff_edge_band": (
            round(float(diff[band].mean()), 3) if band_pixels else 0.0
        ),
        "edge_band_pixels": band_pixels,
    }


__all__ = ["edge_band", "edge_band_mae", "edge_metrics", "load_gray"]
