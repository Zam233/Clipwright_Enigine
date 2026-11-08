"""V34 抗锯齿度量单测：指标必须能区分"抗锯齿边缘"和"硬边放大"。

用构造图验证，不跑 4K 渲染（真机产物在 `out/v34/`，由 `scripts/check_v34_4k.py` 生成）。
"""

from __future__ import annotations

import pytest

from tools.aliasing import edge_band_mae, edge_metrics, load_gray


def _disc_image(tmp_path, *, anti_alias: bool, size: int = 240, radius: float = 70.0):
    """画一个圆盘：anti_alias=True 时边缘有连续灰阶，否则是硬边（2 值）。"""
    from PIL import Image, ImageDraw

    scale = 4 if anti_alias else 1
    big = Image.new("L", (size * scale, size * scale), 0)
    draw = ImageDraw.Draw(big)
    cx = cy = size * scale / 2
    draw.ellipse(
        (cx - radius * scale, cy - radius * scale, cx + radius * scale, cy + radius * scale),
        fill=255,
    )
    image = big.resize((size, size), Image.LANCZOS) if anti_alias else big.resize(
        (size, size), Image.NEAREST
    )
    path = tmp_path / ("aa.png" if anti_alias else "hard.png")
    image.save(path)
    return path


class TestEdgeMetrics:
    def test_anti_aliased_edges_have_mid_tones(self, tmp_path) -> None:
        aa = edge_metrics(load_gray(_disc_image(tmp_path, anti_alias=True)))
        hard = edge_metrics(load_gray(_disc_image(tmp_path, anti_alias=False)))
        # 判据是**相对**的：抗锯齿版的边缘中间调必须显著多于硬边版
        assert aa["mid_tone_ratio"] > hard["mid_tone_ratio"] * 3
        assert aa["mid_tone_ratio"] > 0.05

    def test_hard_edges_are_mostly_binary(self, tmp_path) -> None:
        hard = edge_metrics(load_gray(_disc_image(tmp_path, anti_alias=False)))
        assert hard["mid_tone_ratio"] < 0.15

    def test_blank_image_is_safe(self, tmp_path) -> None:
        from PIL import Image

        path = tmp_path / "blank.png"
        Image.new("L", (64, 64), 0).save(path)
        metrics = edge_metrics(load_gray(path))
        assert metrics["edge_band_pixels"] == 0
        assert metrics["mid_tone_ratio"] == 0.0


class TestEdgeBandMae:
    def test_identical_images_have_zero_error(self, tmp_path) -> None:
        aa = _disc_image(tmp_path, anti_alias=True)
        result = edge_band_mae(aa, aa, aa)
        assert result["mean_abs_diff_all"] == 0.0
        assert result["mean_abs_diff_edge_band"] == 0.0

    def test_hard_edge_differs_more_than_aa(self, tmp_path) -> None:
        """参考图 = 超采样缩回（近似真值）；AA 版应比硬边版更接近它。"""
        from PIL import Image

        aa = _disc_image(tmp_path, anti_alias=True)
        hard = _disc_image(tmp_path, anti_alias=False)
        reference = tmp_path / "ref.png"
        Image.open(aa).save(reference)
        aa_err = edge_band_mae(aa, reference, aa)["mean_abs_diff_edge_band"]
        hard_err = edge_band_mae(hard, reference, aa)["mean_abs_diff_edge_band"]
        assert aa_err < hard_err

    def test_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_gray(tmp_path / "nope.png")
