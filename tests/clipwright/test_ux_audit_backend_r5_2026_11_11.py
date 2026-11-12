"""审计 PRD 2026-11-09 后端第五轮定向回归（BE-06 / BE-07）。

- BE-06：/render/start 非法渲染参数在提交时 4xx（字段级），不进队列
- BE-07：pipeline trace 支持 since_seq 续传（不再从 0 全量重放）
"""

from __future__ import annotations

from clipwright.api.render import _validate_render_params


def _base(**kw) -> dict:
    p = {
        "width": 1920, "height": 1080, "fps": 30.0,
        "bitrate": "cq26", "audio_bitrate": "128k",
        "encoder": "", "pix_fmt": "", "container_ext": "mp4",
    }
    p.update(kw)
    return p


def test_valid_defaults_pass():
    assert _validate_render_params(_base()) == []


def test_invalid_fps_reported_with_field_name():
    errors = _validate_render_params(_base(fps=0))
    assert errors and "fps" in errors[0]


def test_invalid_width_height_reported():
    errors = _validate_render_params(_base(width=8, height=99999))
    assert any("width" in e for e in errors)
    assert any("height" in e for e in errors)


def test_invalid_bitrate_and_audio_bitrate():
    errors = _validate_render_params(_base(bitrate="fast", audio_bitrate="high"))
    assert any("bitrate" in e for e in errors)
    assert any("audio_bitrate" in e for e in errors)


def test_valid_abr_bitrate_accepted():
    assert _validate_render_params(_base(bitrate="5M", audio_bitrate="192k")) == []


def test_invalid_pix_fmt_and_encoder_rejected():
    errors = _validate_render_params(_base(pix_fmt="../evil", encoder="ff; rm -rf /"))
    assert any("pix_fmt" in e for e in errors)
    assert any("encoder" in e for e in errors)


def test_errors_are_human_readable_single_line():
    errors = _validate_render_params(_base(fps="abc"))
    assert all(isinstance(e, str) and "\n" not in e for e in errors)


# ── BE-07：trace 续传游标 ──────────────────────────────────────────────

def test_trace_stream_accepts_since_seq():
    """源码级断言：task_stream 的 cursor 来自 since_seq，而不是写死 0。"""
    import inspect

    from clipwright.api import pipeline as pl

    src = inspect.getsource(pl)
    assert 'request.query_params.get("since_seq")' in src
    # 旧实现是字面量 `cursor = 0`；新实现只允许由 since_seq 派生
    assert "cursor = 0\n" not in src, "旧实现写死 cursor = 0 会导致断线全量重放"


def test_trace_stream_checks_disconnect():
    """源码级断言：流循环必须检测客户端断连，避免协程空转。"""
    import inspect

    from clipwright.api import pipeline as pl

    src = inspect.getsource(pl)
    assert "request.is_disconnected()" in src
