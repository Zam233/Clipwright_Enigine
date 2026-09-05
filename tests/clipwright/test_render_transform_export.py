"""V4: 静态变换导出——metadata.transform (translate/scale/rotate) 进 trim 滤镜链。"""

from __future__ import annotations

from pathlib import Path

import pytest

import clipwright.services.render as render_mod
from clipwright.services.render import RenderService


def _make_rs(tmp_path: Path):
    rs = RenderService(work_dir=tmp_path / "w")
    captured: list[list[str]] = []
    rs._source_valid = lambda p: True  # type: ignore[method-assign]

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"v" * 2048)
        return type("R", (), {"returncode": 0, "stderr": b""})()

    rs._run_ff = fake_run  # type: ignore[method-assign]
    return rs, captured


@pytest.mark.asyncio
async def test_trim_static_transform_uses_overlay(tmp_path, monkeypatch) -> None:
    """scale/rotate/translate → filter_complex 黑底 overlay 链（与预览语义一致）。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [{"source_path": str(tmp_path / "a.mp4"), "duration_sec": 2.0,
             "metadata": {"transform": {"x": 0.1, "y": -0.05, "scale": 1.5, "rotation": 12}}}]
    out = await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)
    assert out and Path(out[0]).exists()

    cmd = captured[0]
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 1920*1.5=2880 → 取偶 2880；1080*1.5=1620
    assert "scale=2880:1620" in fc
    assert "rotate=12.0000*PI/180:c=black:ow=2880:oh=1620" in fc
    # ox = (1920-2880)/2 + 0.1*1920 = -288；oy = (1080-1620)/2 - 0.05*1080 = -324
    assert "overlay=x=-288:y=-324" in fc


@pytest.mark.asyncio
async def test_trim_identity_transform_keeps_vf_path(tmp_path, monkeypatch) -> None:
    """无变换/恒等变换 → 走原 -vf 快路径，不引入 overlay 重链。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [{"source_path": str(tmp_path / "b.mp4"), "duration_sec": 2.0,
             "metadata": {"transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0}}}]
    await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)

    cmd = captured[0]
    assert "-vf" in cmd
    assert "-filter_complex" not in cmd


@pytest.mark.asyncio
async def test_trim_cache_key_distinguishes_transform(tmp_path, monkeypatch) -> None:
    """不同 transform 值必须产生不同的缓存键（否则错误命中旧产物）。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [
        {"source_path": str(tmp_path / "c.mp4"), "duration_sec": 2.0,
         "metadata": {"transform": {"scale": 1.2}}},
        {"source_path": str(tmp_path / "c.mp4"), "duration_sec": 2.0,
         "metadata": {"transform": {"scale": 1.3}}},
    ]
    await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)
    assert len(captured) == 2, "不同 transform 不应共享缓存"
    assert "scale=1.2" not in captured[1][captured[1].index("-filter_complex") + 1]


@pytest.mark.asyncio
async def test_trim_keyframe_transform_expression_overlay(tmp_path, monkeypatch) -> None:
    """V3b: transform 关键帧 → overlay/scale 表达式逐帧求值（clip_local 比例单位）。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [{"source_path": str(tmp_path / "d.mp4"), "duration_sec": 4.0,
             "metadata": {"kf_time_base": "clip_local"},
             "keyframes": [
                 {"time": 0, "properties": {"translate_x": 0.0}},
                 {"time": 2, "properties": {"translate_x": 0.25}, "easing": "ease-out-cubic"},
             ]}]
    await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)

    cmd = captured[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    # overlay 表达式：比例 translate × main_w（引号内含逗号的 if 表达式）
    assert "overlay=x=" in fc
    assert "main_w" in fc
    assert "ease" in fc or "pow(" in fc
    # 有 overlay 链必有黑底画布
    assert "color=c=black:s=1920:1080" in fc


@pytest.mark.asyncio
async def test_trim_keyframe_opacity_true_interpolation(tmp_path, monkeypatch) -> None:
    """V3b: opacity 关键帧 → 单个 colorchannelmixer 分段表达式（非 0.1s 窗口近似）。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [{"source_path": str(tmp_path / "e.mp4"), "duration_sec": 3.0,
             "metadata": {"kf_time_base": "clip_local"},
             "keyframes": [
                 {"time": 0, "properties": {"opacity": 0}},
                 {"time": 1, "properties": {"opacity": 1}},
             ]}]
    await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)

    cmd = captured[0]
    vf_idx = cmd.index("-vf")
    vf = cmd[vf_idx + 1]
    assert "colorchannelmixer=aa=if(" in vf
    assert "between(t,0,0.1)" not in vf, "旧 0.1s 窗口近似应被替换"


@pytest.mark.asyncio
async def test_trim_keyframe_speed_piecewise(tmp_path, monkeypatch) -> None:
    """V3c: speed 关键帧 → 分段恒速 trim+concat（每段 setpts=PTS-START)/v）。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [{"source_path": str(tmp_path / "f.mp4"), "duration_sec": 4.0,
             "source_offset": 1.0,
             "metadata": {"kf_time_base": "clip_local"},
             "keyframes": [
                 {"time": 0, "properties": {"speed": 1.0}},
                 {"time": 2, "properties": {"speed": 2.0}},
                 {"time": 4, "properties": {"speed": 1.0}},
             ]}]
    out = await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)
    assert out and Path(out[0]).exists()

    cmd = captured[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    # 2 段：[0,2] v=1.5、[2,4] v=1.5 —— 平均速度 1.5
    assert fc.count("trim=start=") == 2
    assert "setpts=(PTS-START)/1.5000" in fc
    assert "concat=n=2:v=1:a=0" in fc
    # 源区间累计：offset=1 → 段1源 [1,4]；段2 [4,7]
    assert "trim=start=1.0000:end=4.0000" in fc
    assert "trim=start=4.0000:end=7.0000" in fc


@pytest.mark.asyncio
async def test_trim_cache_key_distinguishes_keyframes(tmp_path, monkeypatch) -> None:
    """V3: 关键帧变化必须进缓存键。"""
    monkeypatch.setattr(render_mod, "_is_valid_video", lambda p: True)
    render_mod._trim_cache.clear()
    rs, captured = _make_rs(tmp_path)

    segs = [
        {"source_path": str(tmp_path / "g.mp4"), "duration_sec": 2.0,
         "metadata": {"kf_time_base": "clip_local"},
         "keyframes": [{"time": 0, "properties": {"opacity": 0}},
                       {"time": 1, "properties": {"opacity": 1}}]},
        {"source_path": str(tmp_path / "g.mp4"), "duration_sec": 2.0,
         "metadata": {"kf_time_base": "clip_local"},
         "keyframes": [{"time": 0, "properties": {"opacity": 1}},
                       {"time": 1, "properties": {"opacity": 0.5}}]},
    ]
    await rs._trim_segments_parallel(segs, 1920, 1080, 30, "5M", "libx264", "medium", None)
    assert len(captured) == 2, "不同关键帧不应共享缓存"
