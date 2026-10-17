"""O-遮罩升级：形状蒙版的表达式生成与真实渲染验证。

覆盖：
- 纯函数 `mask_alpha_expression`：rect/ellipse/polygon 的表达式形状、羽化带宽、
  反相为 `255 - a`、点数夹到 3-12、star/heart 返回警告、非法输入不炸；
- 真实 ffmpeg 渲染：椭圆 / 反相椭圆 / 六边形蒙版的中心与角落亮度对比，
  确认蒙版真的生效（而不只是过滤器字符串拼对了）。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.services.render import RenderService, mask_alpha_expression

RECT = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


class TestMaskExpression:
    def test_rect_mask_expression(self) -> None:
        expr, warn = mask_alpha_expression("rect", RECT, width=1920, height=1080)
        assert warn is None
        assert expr is not None
        # 中心 (960, 540)、半宽 480、半高 270
        assert "X-(480.00)" in expr and "(1440.00)-X" in expr
        assert "Y-(270.00)" in expr and "(810.00)-Y" in expr
        assert expr.startswith("if(gte(")

    def test_rect_feather_uses_linear_ramp(self) -> None:
        expr, _ = mask_alpha_expression("rect", RECT, feather=8, width=1920, height=1080)
        assert expr is not None
        assert "clip(" in expr and "/8.00" in expr and "+0.5" in expr

    def test_ellipse_expression(self) -> None:
        expr, _ = mask_alpha_expression("ellipse", RECT, width=1920, height=1080)
        assert expr is not None
        assert "pow(X-960.00,2)/pow(480.00,2)" in expr
        assert "pow(Y-540.00,2)/pow(270.00,2)" in expr

    def test_ellipse_feather_band_shrinks_with_radius(self) -> None:
        small, _ = mask_alpha_expression(
            "ellipse", {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}, feather=10, width=1920, height=1080)
        big, _ = mask_alpha_expression(
            "ellipse", {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, feather=10, width=1920, height=1080)
        # 相同的像素羽化宽度，在小形状上占用更大的归一化带宽
        def band(expr: str) -> float:
            import re

            m = re.search(r"1\+([0-9.]+)-", expr)
            assert m, expr
            return float(m.group(1))

        assert band(small) > band(big)

    def test_inverted_wraps_body(self) -> None:
        normal, _ = mask_alpha_expression("ellipse", RECT, width=1920, height=1080)
        inverted, _ = mask_alpha_expression("ellipse", RECT, inverted=True, width=1920, height=1080)
        assert inverted == f"255-({normal})"

    def test_polygon_has_one_term_per_side(self) -> None:
        for sides in (3, 5, 6, 12):
            expr, warn = mask_alpha_expression(
                "polygon", RECT, points=sides, width=1920, height=1080)
            assert warn is None and expr is not None
            # 每条边一个内法向判定子式
            assert expr.count("if(gte(") == sides or expr.count("clip(") == sides

    def test_polygon_points_clamped(self) -> None:
        too_few, _ = mask_alpha_expression("polygon", RECT, points=1, width=100, height=100)
        too_many, _ = mask_alpha_expression("polygon", RECT, points=99, width=100, height=100)
        assert too_few is not None and too_many is not None
        assert too_few.count("if(gte(") == 3      # 夹到 3
        assert too_many.count("if(gte(") == 12     # 夹到 12

    def test_polygon_rotation_changes_expression(self) -> None:
        flat, _ = mask_alpha_expression("polygon", RECT, points=4, width=100, height=100)
        rotated, _ = mask_alpha_expression(
            "polygon", RECT, points=4, rotation=45, width=100, height=100)
        assert flat != rotated

    def test_polygon_feather_is_product_of_ramps(self) -> None:
        expr, _ = mask_alpha_expression(
            "polygon", RECT, points=5, feather=6, width=1920, height=1080)
        assert expr is not None
        assert expr.count("clip(") == 5
        assert expr.startswith("255*(")

    def test_star_and_heart_unsupported_with_warning(self) -> None:
        for kind in ("star", "heart"):
            expr, warn = mask_alpha_expression(kind, RECT, width=100, height=100)
            assert expr is None
            assert warn and kind in warn and "预览" in warn

    def test_invalid_rect_or_degenerate_size(self) -> None:
        assert mask_alpha_expression("rect", None, width=100, height=100) == (None, None)
        assert mask_alpha_expression("rect", "nope", width=100, height=100) == (None, None)  # type: ignore[arg-type]
        assert mask_alpha_expression("rect", {"x": "a"}, width=100, height=100) == (None, None)
        tiny, _ = mask_alpha_expression(
            "rect", {"x": 0, "y": 0, "w": 0, "h": 0}, width=100, height=100)
        assert tiny is None

    def test_unknown_kind(self) -> None:
        assert mask_alpha_expression("hexagon", RECT, width=100, height=100) == (None, None)


pytest_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要本机 ffmpeg/ffprobe",
)


def _clip(path: Path, seconds: float) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


def _frame_luma(video: str, at: float, points: list[tuple[float, float]], tmp: Path) -> list[float]:
    """抽一帧，返回若干**归一化坐标**处的亮度（0-255）。"""
    png = tmp / f"f_{at:.2f}.png".replace(":", "_")
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", video,
         "-frames:v", "1", str(png)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]
    from PIL import Image

    out: list[float] = []
    with Image.open(png) as img:
        rgb = img.convert("RGB")
        for fx, fy in points:
            x = min(rgb.width - 1, max(0, int(fx * rgb.width)))
            y = min(rgb.height - 1, max(0, int(fy * rgb.height)))
            r0, g0, b0 = rgb.getpixel((x, y))
            out.append(0.299 * r0 + 0.587 * g0 + 0.114 * b0)
    return out


def _render_mask(tmp_path: Path, mask: dict) -> tuple[object, str]:
    from clipwright.schema.timeline import Timeline

    src = tmp_path / "src.mp4"
    _clip(src, 2.0)
    clip = {
        "id": "c1", "kind": "video", "asset_id": str(src), "track_id": "v0",
        "start_sec": 0.0, "duration_sec": 2.0, "source_offset_sec": 0,
        "speed": 1, "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
        **mask,
    }
    timeline = Timeline(**{
        "id": "mask_case", "width": 320, "height": 240, "fps": 30, "duration_sec": 2,
        "tracks": [{"id": "v0", "name": "v", "kind": "video", "index": 0, "clips": [clip]}],
    })
    svc = RenderService(work_dir=tmp_path / "engine")
    out = tmp_path / "out.mp4"
    res = asyncio.run(svc.render(timeline, out, width=320, height=240, fps=30))
    return res, str(out)


@pytest_ffmpeg
class TestMaskRender:
    """真实渲染：蒙版必须改变画面（中心 vs 角落亮度对比）。"""

    def test_ellipse_mask_keeps_center_hides_corner(self, tmp_path: Path) -> None:
        res, out = _render_mask(tmp_path, {
            "mask_type": "ellipse", "mask_rect": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
        })
        assert res.success, res.error
        center, corner = _frame_luma(out, 1.0, [(0.5, 0.5), (0.04, 0.04)], tmp_path)
        assert center > corner + 20, f"椭圆蒙版未生效：中心 {center:.0f} / 角落 {corner:.0f}"

    def test_inverted_ellipse_hides_center_keeps_corner(self, tmp_path: Path) -> None:
        res, out = _render_mask(tmp_path, {
            "mask_type": "ellipse", "mask_rect": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
            "mask_inverted": True,
        })
        assert res.success, res.error
        center, corner = _frame_luma(out, 1.0, [(0.5, 0.5), (0.04, 0.04)], tmp_path)
        assert corner > center + 20, f"反相蒙版未生效：中心 {center:.0f} / 角落 {corner:.0f}"

    def test_polygon_mask_keeps_center_hides_corner(self, tmp_path: Path) -> None:
        res, out = _render_mask(tmp_path, {
            "mask_type": "polygon", "mask_rect": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
            "mask_points": 6,
        })
        assert res.success, res.error
        center, corner = _frame_luma(out, 1.0, [(0.5, 0.5), (0.03, 0.03)], tmp_path)
        assert center > corner + 20, f"多边形蒙版未生效：中心 {center:.0f} / 角落 {corner:.0f}"

    def test_feather_produces_intermediate_edge(self, tmp_path: Path) -> None:
        """羽化：边缘上应出现介于"全显示"与"全透明"之间的过渡带。"""
        res, out = _render_mask(tmp_path, {
            "mask_type": "ellipse", "mask_rect": {"x": 0.15, "y": 0.15, "w": 0.7, "h": 0.7},
            "mask_feather": 40,
        })
        assert res.success, res.error
        center, edge, corner = _frame_luma(
            out, 1.0, [(0.5, 0.5), (0.5, 0.175), (0.02, 0.02)], tmp_path)
        assert center > corner + 20
        # 过渡带亮度介于两者之间（允许一定重叠）
        assert corner - 5 <= edge <= center + 5, f"羽化过渡异常：中心 {center:.0f} 边缘 {edge:.0f} 角落 {corner:.0f}"

    def test_star_mask_warns_and_skips(self, tmp_path: Path) -> None:
        res, out = _render_mask(tmp_path, {
            "mask_type": "star", "mask_rect": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
        })
        assert res.success, res.error
        assert any("star" in w and "预览" in w for w in res.warnings), res.warnings
        # 跳过蒙版 → 中心与角落都可见（没有假装裁剪）
        center, corner = _frame_luma(out, 1.0, [(0.5, 0.5), (0.04, 0.04)], tmp_path)
        assert min(center, corner) > 10

    def test_rect_crop_behaviour_unchanged(self, tmp_path: Path) -> None:
        """未设羽化/反相时，rect 仍是「裁剪填满画幅」——旧行为不变。"""
        res, out = _render_mask(tmp_path, {
            "mask_type": "rect", "mask_rect": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
        })
        assert res.success, res.error
        from clipwright.schema.timeline import Timeline  # noqa: F401

        center, corner = _frame_luma(out, 1.0, [(0.5, 0.5), (0.04, 0.04)], tmp_path)
        # 裁剪填满：角落也是画面内容（不是透明/黑）
        assert corner > 10, f"裁剪路径疑似失效：角落亮度 {corner:.0f}"
        assert center > 10
