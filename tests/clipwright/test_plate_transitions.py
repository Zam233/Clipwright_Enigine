"""转场与腐坏组合单测（PRD C4 / R-14 / R-15）。"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from clipwright.animation.plate import (
    TRANSITIONS,
    decay_sequence,
    shatter,
    slices,
    winclose,
    zoom,
)
from clipwright.animation.plate.transitions import apply


def _solid(color: tuple[int, int, int], size: tuple[int, int] = (80, 60)) -> Image.Image:
    return Image.new("RGBA", size, (*color, 255))


def _alpha_sum(image: Image.Image) -> int:
    return int((np.array(image.convert("RGBA"))[..., 3] > 0).sum())


SRC = _solid((255, 138, 61))
DST = _solid((20, 30, 60))


class TestTransitions:
    def test_all_named_transitions_available(self) -> None:
        assert set(TRANSITIONS) == {"winclose", "zoom", "shatter", "slices"}

    def test_unknown_transition_raises(self) -> None:
        with pytest.raises(KeyError):
            apply("dissolve", SRC, DST, 0.5)

    @pytest.mark.parametrize("name", ["winclose", "zoom", "shatter", "slices"])
    def test_p0_keeps_source_p1_reveals_dest(self, name: str) -> None:
        at_zero = apply(name, SRC, DST, 0.0)
        at_one = apply(name, SRC, DST, 1.0)
        assert at_zero.size == SRC.size
        assert at_one.size == SRC.size
        # p=1 时源帧应基本消失：顶层像素颜色接近目标帧
        if name in {"winclose", "zoom"}:
            assert np.array(at_one)[5, 5, :3].tolist() == [20, 30, 60]

    def test_winclose_shrinks_source(self) -> None:
        middle = winclose(SRC, DST, 0.5)
        # 中心仍是源（或混合），四角已被目标帧占据
        corner = np.array(middle)[0, 0, :3].tolist()
        assert corner == [20, 30, 60]

    def test_zoom_covers_full_frame_at_half(self) -> None:
        middle = zoom(SRC, DST, 0.5)
        assert _alpha_sum(middle) == SRC.size[0] * SRC.size[1]

    def test_shatter_requires_positive_grid(self) -> None:
        with pytest.raises(ValueError):
            shatter(SRC, DST, 0.5, cols=0)

    def test_shatter_spreads_tiles(self) -> None:
        middle = shatter(SRC, DST, 1.0, cols=4, rows=3, spread_px=40)
        # p=1：源帧整体淡出，画面基本是目标帧
        assert np.array(middle)[10, 10, :3].tolist() == [20, 30, 60]

    def test_slices_validates_band_count(self) -> None:
        with pytest.raises(ValueError):
            slices(SRC, DST, 0.5, bands=0)

    def test_slices_shift_strips(self) -> None:
        edge = _solid((255, 255, 255))
        out = slices(edge, DST, 0.5, bands=8, max_offset_px=30)
        assert out.size == edge.size

    def test_deterministic(self) -> None:
        a = np.array(slices(SRC, DST, 0.4))
        b = np.array(slices(SRC, DST, 0.4))
        assert np.array_equal(a, b)


class TestDecaySequence:
    def test_hold_modulus_repeats_frames(self) -> None:
        frames = [_solid((i * 20, 0, 0)) for i in range(6)]
        out = decay_sequence(frames, hold_modulus=3)
        assert len(out) == 6
        assert np.array_equal(np.array(out[1]), np.array(out[0]))
        assert np.array_equal(np.array(out[3]), np.array(out[3]))
        assert not np.array_equal(np.array(out[3]), np.array(out[0]))

    def test_noop_returns_same_objects(self) -> None:
        frames = [_solid((10, 10, 10)), _solid((20, 20, 20))]
        assert decay_sequence(frames) == frames

    def test_misregistration_progresses(self) -> None:
        frames = [_solid((i * 10, 50, 50)) for i in range(5)]
        out = decay_sequence(frames, misregistration_px=20)
        # 第一帧不该被撕裂（offset=0），最后一帧应被撕裂
        assert np.array_equal(np.array(out[0]), np.array(frames[0]))
        assert not np.array_equal(np.array(out[-1]), np.array(frames[-1]))
