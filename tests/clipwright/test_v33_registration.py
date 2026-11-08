"""V33 剪影配准精度：估计器单测（已知真值的合成剪影）。

真机取证在 `scripts/check_v33_registration.py`；这里锁住"估计器本身准不准"。
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from clipwright.animation.plate.registration import Registration, apply_registration
from tools.registration_check import compare_to_truth, estimate_registration

CANVAS = (400, 500)
BOX = (120, 150, 280, 360)


def _figure() -> Image.Image:
    box_w = BOX[2] - BOX[0]
    box_h = BOX[3] - BOX[1]
    figure = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(figure)
    draw.ellipse((box_w * 0.25, box_h * 0.05, box_w * 0.75, box_h * 0.45),
                 fill=(30, 30, 30, 255))
    draw.ellipse((box_w * 0.10, box_h * 0.45, box_w * 0.90, box_h * 0.95),
                 fill=(30, 30, 30, 255))
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    canvas.alpha_composite(figure, (BOX[0], BOX[1]))
    return canvas


class TestEstimate:
    def test_identical_images(self) -> None:
        base = _figure()
        estimate = estimate_registration(base, base)
        assert estimate.scale == pytest.approx(1.0, abs=1e-6)
        assert estimate.tx == pytest.approx(0.0, abs=1e-6)
        assert estimate.ty == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize("truth", [
        Registration(scale=0.9, tx=17, ty=-12),
        Registration(scale=0.95, tx=-9, ty=6),
        Registration(scale=0.85, tx=24, ty=-18),
    ])
    def test_recovers_known_transform_within_one_pixel(self, truth) -> None:
        base = _figure()
        probe = apply_registration(base, truth, CANVAS)
        result = compare_to_truth(estimate_registration(base, probe), truth)
        assert result["ok"] is True, result["errors"]
        assert result["errors"]["tx_delta"] <= 1.0
        assert result["errors"]["ty_delta"] <= 1.0

    def test_blank_image_raises(self) -> None:
        with pytest.raises(ValueError):
            estimate_registration(_figure(), Image.new("RGBA", CANVAS, (0, 0, 0, 0)))


class TestCompare:
    def test_flags_oversized_error(self) -> None:
        from tools.registration_check import Estimate

        result = compare_to_truth(
            Estimate(scale=1.0, tx=3.0, ty=0.0), Registration(scale=1.0, tx=0, ty=0)
        )
        assert result["ok"] is False
        assert result["errors"]["tx_delta"] == 3.0

    def test_reports_rounding(self) -> None:
        from tools.registration_check import Estimate

        result = compare_to_truth(
            Estimate(scale=0.90004, tx=0.4, ty=-0.6), Registration(scale=0.9, tx=0, ty=0)
        )
        assert result["ok"] is True
        assert result["errors"]["tx_delta"] == 0.4
