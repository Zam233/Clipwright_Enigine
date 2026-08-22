"""drawtext 文本转义单元测试（审计 P0 修复回归）。

转义规则经 ffmpeg 8.x 实机渲染校准（见 docs 审计记录）：
- 裸 % / 单反斜杠 / 裸冒号 / 单引号旧写法 均会导致文本消失或解析错误；
- 新规则：% -> \\%，\\ -> \\\\，: -> \\:，' -> ’，换行 -> 空格。
"""

from __future__ import annotations

from clipwright.tool.design import color_to_drawtext, escape_drawtext_text


def test_percent_escaped():
    assert escape_drawtext_text("折扣50%") == "折扣50\\\\%"


def test_backslash_quadrupled():
    assert escape_drawtext_text("a\\b") == "a\\\\\\\\b"


def test_colon_escaped():
    assert escape_drawtext_text("10:30") == "10\\:30"


def test_apostrophe_substituted():
    assert escape_drawtext_text("it's") == "it’s"


def test_newlines_to_space():
    assert escape_drawtext_text("a\r\nb\nc\rd") == "a b c d"


def test_combined_order_stable():
    # 反斜杠先翻 4 倍；% 与 : 引入的反斜杠不再二次翻倍
    # 输入: \ % :  →  输出: 4×\ + 2×\ + % + 1×\ + :
    assert escape_drawtext_text("\\%:") == "\\" * 6 + "%" + "\\:"


def test_plain_text_unchanged():
    assert escape_drawtext_text("Hello 世界，；[]") == "Hello 世界，；[]"


def test_color_invalid_falls_back_white():
    assert color_to_drawtext("red") == "0xFFFFFF"
    assert color_to_drawtext("") == "0xFFFFFF"
    assert color_to_drawtext(None) == "0xFFFFFF"


def test_color_valid_passthrough():
    assert color_to_drawtext("#FF0000") == "0xFF0000"
    assert color_to_drawtext("#FF000080") == "0xFF0000@0.502"
    assert color_to_drawtext("0x00FF00") == "0x00FF00"
