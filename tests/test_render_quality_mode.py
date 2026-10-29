"""质量模式（CQ/CRF）与导出预设的回归。

用户 2026-10-28：同一份 702s 工程，`-c copy` 26MB vs 固定 5M 重编 299MB。
默认改成恒定质量 CQ26，并允许前端显式传码率。
"""

from __future__ import annotations

from clipwright.api.render import _EXPORT_PRESETS, RenderSettings
from clipwright.services.render import parse_quality, video_rate_args


def test_parse_quality_accepts_cq_and_crf():
    assert parse_quality("cq26") == 26
    assert parse_quality("CQ18") == 18
    assert parse_quality("crf23") == 23
    assert parse_quality("cq:20") == 20


def test_parse_quality_rejects_abr():
    assert parse_quality("5M") is None
    assert parse_quality("3000k") is None
    assert parse_quality("") is None
    assert parse_quality("cq99") is None  # 越界


def test_nvenc_uses_cq_vbr():
    args = video_rate_args("h264_nvenc", "cq26")
    assert args == ["-rc", "vbr", "-cq", "26", "-b:v", "0"], (
        "NVENC 恒定质量必须给 -b:v 0，否则退回 ABR"
    )


def test_x264_uses_crf():
    assert video_rate_args("libx264", "cq26") == ["-crf", "26"]
    assert video_rate_args("libx265", "crf20") == ["-crf", "20"]


def test_abr_passthrough():
    assert video_rate_args("h264_nvenc", "5M") == ["-b:v", "5M"]
    assert video_rate_args("libx264", "3000k") == ["-b:v", "3000k"]


def test_prores_has_no_rate_arg():
    assert video_rate_args("prores_ks", "cq26") == []


def test_default_render_settings_are_cq26():
    s = RenderSettings()
    assert s.bitrate == "cq26", "默认必须是恒定质量（体积约为 5M 的一半）"
    assert s.audio_bitrate == "128k"


def test_1080p_preset_uses_cq26():
    preset = _EXPORT_PRESETS["1080p"]
    assert preset["bitrate"] == "cq26"
    for name in ("master_cq18", "web_cq26", "web_cq30"):
        assert name in _EXPORT_PRESETS, f"{name} 供前端下拉选择"
