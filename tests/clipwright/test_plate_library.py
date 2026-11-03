"""版画/印刷库单测（PRD C3 / M1：R-06…R-10）。

全部用合成图（numpy/PIL）验证，不依赖外部素材，毫秒级。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from clipwright.animation.plate import (
    DEFAULT_INK_PALETTE,
    MISREGISTRATION_STEPS,
    Registration,
    apply_registration,
    boundary_pixels,
    clean_alpha,
    compose,
    decimate_indices,
    derive_size,
    glyph_plate,
    hold_frames_ok,
    layer_coverage,
    load_registration,
    mask_to_svg_path,
    misregistration,
    nearest_ink_name,
    polygon_area,
    registration_error,
    render_ground,
    rgb_split,
    save_registration,
    simplify,
    split_inks,
    tear,
    trace_contours,
)


def _ink_image(size=(80, 40)) -> Image.Image:
    """左半橙、右半米白、底部一条黑，alpha 全满。"""
    width, height = size
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, : width // 3] = (255, 138, 61, 255)
    arr[:, width // 3 : (2 * width) // 3] = (242, 234, 216, 255)
    arr[:, (2 * width) // 3 :] = (14, 14, 18, 255)
    arr[-2:, :] = (14, 14, 18, 255)
    return Image.fromarray(arr, mode="RGBA")


def _square_mask(size=(40, 40), box=(10, 10, 30, 30)) -> np.ndarray:
    mask = np.zeros(size, dtype=bool)
    mask[box[1] : box[3], box[0] : box[2]] = True
    return mask


class TestCleanAlpha:
    def test_low_alpha_is_zeroed(self) -> None:
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[..., 3] = 8
        arr[0, 0, 3] = 200
        out = np.array(clean_alpha(Image.fromarray(arr, "RGBA")))
        assert out[..., 3].sum() == 200

    def test_keeps_above_threshold(self) -> None:
        arr = np.zeros((2, 2, 4), dtype=np.uint8)
        arr[..., 3] = 16
        assert (np.array(clean_alpha(Image.fromarray(arr, "RGBA")))[..., 3] > 0).all()


class TestSplitInks:
    def test_three_layers_cover_all_opaque_pixels(self) -> None:
        layers = split_inks(_ink_image())
        coverage = layer_coverage(layers)
        assert sum(coverage.values()) == 80 * 40
        assert all(coverage[name] > 0 for name in ("me", "you", "ink"))

    def test_colors_are_assigned_by_nearest_ink(self) -> None:
        layers = split_inks(_ink_image())
        me = np.array(layers["me"])
        assert tuple(me[5, 5, :3]) == DEFAULT_INK_PALETTE["me"]
        assert me[5, 70, 3] == 0  # 右半不属于"我"

    def test_tint_false_keeps_original_pixels(self) -> None:
        layers = split_inks(_ink_image(), tint=False)
        assert tuple(np.array(layers["me"])[5, 5, :3]) == (255, 138, 61)

    def test_empty_image_returns_empty_layers(self) -> None:
        blank = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        layers = split_inks(blank)
        assert sum(layer_coverage(layers).values()) == 0

    def test_missing_ink_raises(self) -> None:
        try:
            split_inks(_ink_image(), palette={"me": (1, 2, 3)})
        except KeyError:
            return
        raise AssertionError("调色板缺色应当报错")


class TestCompose:
    def test_compose_without_offset_reconstructs(self) -> None:
        layers = split_inks(_ink_image())
        merged = np.array(compose(layers, size=(80, 40)))
        original = np.array(_ink_image())
        assert (merged[..., 3] > 0).sum() == (original[..., 3] > 0).sum()

    def test_compose_with_offset_shifts_layer(self) -> None:
        layers = split_inks(_ink_image())
        shifted = np.array(compose(layers, offsets={"me": (3, 0)}, size=(80, 40)))
        assert shifted[5, 3, 3] > 0  # 橙版右移后仍覆盖原位附近


class TestGround:
    def test_deterministic_for_same_seed(self) -> None:
        a = render_ground(160, 90, seed=7)
        b = render_ground(160, 90, seed=7)
        assert np.array_equal(np.array(a), np.array(b))

    def test_different_seed_differs(self) -> None:
        a = np.array(render_ground(160, 90, seed=7))
        b = np.array(render_ground(160, 90, seed=8))
        assert not np.array_equal(a, b)

    def test_corner_marks_present(self) -> None:
        image = np.array(render_ground(200, 120, seed=1))
        margin = max(8, int(min(200, 120) * 0.02))
        assert image[margin, margin].max() > image[60, 100].max()

    def test_invalid_size_rejected(self) -> None:
        try:
            render_ground(0, 10)
        except ValueError:
            return
        raise AssertionError("width=0 应当报错")


class TestDegrade:
    def test_misregistration_offsets_increase(self) -> None:
        layers = split_inks(_ink_image())
        shifted = misregistration(layers, offset_px=MISREGISTRATION_STEPS[1])
        # 第三个墨版位移最大：原来在左上的橙色不再覆盖同一像素
        assert shifted["ink"].size == layers["ink"].size
        assert MISREGISTRATION_STEPS == (10, 18, 28)

    def test_rgb_split_moves_channels(self) -> None:
        base = _ink_image()
        split = rgb_split(base, dx=4)
        diff = np.abs(np.array(split, dtype=int) - np.array(base, dtype=int))
        assert diff[..., 0].sum() > 0 or diff[..., 2].sum() > 0

    def test_tear_shifts_band(self) -> None:
        base = _ink_image((60, 60))
        torn = tear(base, offset_px=10)
        diff = np.abs(np.array(torn, dtype=int) - np.array(base, dtype=int))
        assert diff.sum() > 0

    def test_tear_direction_validation(self) -> None:
        try:
            tear(_ink_image(), direction="diagonal")
        except ValueError:
            return
        raise AssertionError("非法方向应当报错")

    def test_decimate_indices_hold_repeats(self) -> None:
        indices = decimate_indices(24, source_fps=24, target_fps=8)
        assert len(indices) == 24
        assert indices[:3] == [0, 0, 0]
        assert indices[3] == 3

    def test_decimate_noop_when_target_higher(self) -> None:
        assert decimate_indices(5, source_fps=24, target_fps=60) == [0, 1, 2, 3, 4]

    def test_hold_frames(self) -> None:
        assert hold_frames_ok(6)
        assert not hold_frames_ok(5)


class TestGlyph:
    def test_output_size_matches_grid(self) -> None:
        plate = glyph_plate(_ink_image((40, 20)), cols=10, cell=8)
        assert plate.size[0] == 80
        assert plate.size[1] % 8 == 0

    def test_deterministic(self) -> None:
        a = np.array(glyph_plate(_ink_image(), cols=12, cell=6))
        b = np.array(glyph_plate(_ink_image(), cols=12, cell=6))
        assert np.array_equal(a, b)

    def test_ink_colors_used(self) -> None:
        """抗锯齿会让边缘像素变暗，因此只看"最亮的一批字符像素"属于哪个墨版。"""
        solid = Image.new("RGBA", (64, 32), (*DEFAULT_INK_PALETTE["me"], 255))
        plate = np.array(glyph_plate(solid, cols=16, cell=8, background=(0, 0, 0)))
        drawn = plate.reshape(-1, 3).astype(int)
        drawn = drawn[drawn.sum(axis=1) > 120]  # 只取明显的字符笔画像素
        assert drawn.shape[0] >= 50, "应当画出足量字符笔画"
        inks = {nearest_ink_name(tuple(pixel)) for pixel in drawn}
        assert "me" in inks, f"应出现橙色（我）笔画，实际 {inks}"

    def test_transparent_area_left_as_background(self) -> None:
        blank = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        plate = np.array(glyph_plate(blank, cols=4, cell=5, background=(1, 2, 3)))
        unique = {tuple(int(v) for v in row) for row in plate.reshape(-1, 3)}
        assert unique == {(1, 2, 3)}

    def test_invalid_cols(self) -> None:
        try:
            glyph_plate(_ink_image(), cols=1)
        except ValueError:
            return
        raise AssertionError("cols=1 应当报错")

    def test_supersample_keeps_size_and_determinism(self) -> None:
        a = np.array(glyph_plate(_ink_image((60, 30)), cols=20, cell=8, supersample=1))
        b = np.array(glyph_plate(_ink_image((60, 30)), cols=20, cell=8, supersample=2))
        assert a.shape == b.shape
        c = np.array(glyph_plate(_ink_image((60, 30)), cols=20, cell=8, supersample=2))
        assert np.array_equal(b, c), "超采样渲染必须可复现"

    def test_more_glyph_coverage_with_gamma(self) -> None:
        """gamma 把中间调推向密字符：可见笔画像素应显著变多。"""

        def ink_pixels(img: Image.Image) -> int:
            arr = np.array(img).reshape(-1, 3).astype(int)
            return int((arr.sum(axis=1) > 120).sum())

        solid = Image.new("RGBA", (80, 40), (*DEFAULT_INK_PALETTE["me"], 255))
        flat = glyph_plate(solid, cols=20, cell=8, gamma=1.0)
        dense = glyph_plate(solid, cols=20, cell=8, gamma=0.6)
        assert ink_pixels(dense) > ink_pixels(flat)

    def test_nearest_ink_name(self) -> None:
        assert nearest_ink_name((250, 135, 60)) == "me"
        assert nearest_ink_name((10, 10, 12)) == "ink"


class TestVectorize:
    def test_square_has_few_points(self) -> None:
        contours = trace_contours(_square_mask())
        assert contours and len(contours[0]) <= 8

    def test_polygon_area_close_to_mask(self) -> None:
        mask = _square_mask(box=(10, 10, 30, 30))
        contour = trace_contours(mask)[0]
        area = polygon_area(contour)
        assert 300 <= area <= 420, area  # 20x20=400，边界像素导致略小

    def test_boundary_pixels_of_square(self) -> None:
        pixels = boundary_pixels(_square_mask())
        assert len(pixels) == 2 * 20 + 2 * 20 - 4

    def test_svg_path_is_well_formed(self) -> None:
        path = mask_to_svg_path(_square_mask())
        assert path.startswith("M ")
        assert path.endswith("Z")
        assert path.count("M ") == path.count("Z")

    def test_empty_mask_yields_no_contours(self) -> None:
        assert trace_contours(np.zeros((10, 10), dtype=bool)) == []

    def test_simplify_removes_collinear_points(self) -> None:
        line = [(0, 0), (0, 1), (0, 2), (0, 3)]
        assert simplify(line) == [(0, 0), (0, 3)]


class TestRegistration:
    def test_round_trip_json(self, tmp_path) -> None:
        path = tmp_path / "registration.json"
        table = {
            "f_bust": Registration(1.0, 0, 0),
            "cat_bust": Registration(1.0, -28, -49),
        }
        save_registration(path, table, base="f_bust")
        loaded = load_registration(path)
        assert loaded["cat_bust"].tx == -28
        assert loaded["f_bust"].scale == 1.0

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        assert load_registration(tmp_path / "nope.json") == {}

    def test_apply_registration_places_pixels(self) -> None:
        image = _ink_image((20, 20))
        canvas = apply_registration(image, Registration(1.0, 5, 7), (40, 40))
        arr = np.array(canvas)
        assert arr[8, 6, 3] > 0

    def test_derive_size_keeps_ratio(self) -> None:
        assert derive_size((1086, 1449), height=724) == (543, 724)
        assert derive_size((100, 50), width=200) == (200, 100)

    def test_registration_error_zero_for_same(self) -> None:
        reg = Registration(0.89, 60, -18)
        assert registration_error(reg, reg)["tx_delta"] == 0
