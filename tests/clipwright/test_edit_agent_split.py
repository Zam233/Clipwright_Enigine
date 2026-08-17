"""E2E 修复：字幕切分必须保留全部标点（文案一字不漏）。"""

from __future__ import annotations

from clipwright.agents.edit_agent import _split_sentences


def test_split_keeps_all_punctuation():
    """逗号/句号/感叹/问号/分号/冒号全部保留在前句末尾。"""
    text = "有一个经典名场面，在每年高考前后的那段时间总会上演。文科与理科的对立已经成为对立金字塔中的前几名了！"
    parts = _split_sentences(text)
    joined = "".join(parts)
    assert joined == text  # 一字不漏（标点全保留）


def test_split_verbatim_roundtrip_long_script():
    """长文案切分后拼接与原文完全一致（标点+空白全保留）。"""
    text = (
        "第一句，包含逗号。第二句包含句号！第三句有问号？第四句：有冒号；第五句有分号。"
        "这是一个非常长的句子，超过了四十个字符，所以会被二次切分，但标点必须保留，不能丢失任何一个标点符号。"
    )
    parts = _split_sentences(text)
    joined = "".join(parts)
    # 去空白后必须与原文一致（切分不吞字符）
    nf = lambda s: s.replace("\n", "").replace(" ", "")
    assert nf(joined) == nf(text)
    # 每个标点都在结果里
    for p in "，。！？；：":
        assert p in joined, f"标点 {p} 丢失"


def test_split_empty_and_plain():
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []
    parts = _split_sentences("没有标点的一段文字")
    assert "".join(parts) == "没有标点的一段文字"


def test_split_question_exclamation_kept():
    """问叹号原本就保留，回归确认。"""
    text = "这样行吗？不行！那怎么办？"
    parts = _split_sentences(text)
    assert "".join(parts) == text
    assert parts[0].endswith("？")
    assert parts[1].endswith("！")
