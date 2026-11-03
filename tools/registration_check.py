"""剪影配准精度：从图像估计 ``{scale, tx, ty}``，与已知真值比对（V33）。

验收要求"偏差 ≤1px"。要量这个，需要**已知真值**：拿一张剪影，按已知的缩放与平移生成
探针图，再从两张图估回参数，与真值比。

估计方法刻意用"面积 + 质心"这种可解释的量：

- ``scale`` ≈ sqrt(探针不透明面积 / 基准不透明面积)；
- 把探针按估计的 scale 缩放后，两张图的质心差即 ``tx/ty``。

这比直接比较配准表（`registration_error` 那样）更有意义——它检验的是"从图上能不能量准"，
而不是"两个数写没写一样"。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from clipwright.animation.plate.registration import Registration


@dataclass(frozen=True)
class Estimate:
    scale: float
    tx: float
    ty: float

    def as_dict(self) -> dict[str, float]:
        return {
            "scale": round(self.scale, 4),
            "tx": round(self.tx, 3),
            "ty": round(self.ty, 3),
        }


def _alpha_weights(image: Image.Image) -> np.ndarray:
    """不透明度权重（0–1 连续值）。

    刻意**不用**二值 alpha 阈值：缩放重采样（LANCZOS）会在硬边周围产生振铃，
    阈值化后掩码会偏胖/偏瘦，质心随之漂 2–3px（第一版就是这样把误差做成 3px 的）。
    连续权重对重采样更稳。
    """
    rgba = image.convert("RGBA")
    return np.asarray(rgba)[..., 3].astype(np.float64) / 255.0


def _area_and_centroid(weights: np.ndarray) -> tuple[float, float, float]:
    total = float(weights.sum())
    if total <= 1e-9:
        return 0.0, 0.0, 0.0
    ys, xs = np.nonzero(weights)
    values = weights[ys, xs]
    return total, float((xs * values).sum() / total), float((ys * values).sum() / total)


def estimate_registration(base: Image.Image, probe: Image.Image) -> Estimate:
    """从两张剪影图估计探针相对基准的 ``{scale, tx, ty}``。"""
    base_weights = _alpha_weights(base)
    probe_weights = _alpha_weights(probe)
    base_area, base_cx, base_cy = _area_and_centroid(base_weights)
    probe_area, probe_cx, probe_cy = _area_and_centroid(probe_weights)
    if base_area <= 0 or probe_area <= 0:
        raise ValueError("剪影图为空（alpha 全透明）")
    scale = float(np.sqrt(probe_area / base_area))
    # apply_registration 的几何是"先按左上角缩放，再平移"：x' = x*scale + tx
    tx = probe_cx - base_cx * scale
    ty = probe_cy - base_cy * scale
    return Estimate(scale=scale, tx=tx, ty=ty)


def compare_to_truth(estimate: Estimate, truth: Registration) -> dict:
    """与真值比对，返回逐项误差与是否满足"≤1px"。"""
    errors = {
        "scale_delta": abs(estimate.scale - truth.scale),
        "tx_delta": abs(estimate.tx - truth.tx),
        "ty_delta": abs(estimate.ty - truth.ty),
    }
    return {
        "estimate": estimate.as_dict(),
        "truth": {"scale": truth.scale, "tx": truth.tx, "ty": truth.ty},
        "errors": {key: round(value, 3) for key, value in errors.items()},
        "ok": errors["tx_delta"] <= 1.0 and errors["ty_delta"] <= 1.0,
    }


def save_report(report: dict, path: str | Path) -> Path:
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


__all__ = ["Estimate", "compare_to_truth", "estimate_registration", "save_report"]
