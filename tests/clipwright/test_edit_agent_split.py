"""字幕切分规则：按标点切分，仅保留 ！？，其余标点（，。；：）作为边界消费。"""

from __future__ import annotations

from clipwright.agents.edit_agent import _split_sentences


def test_split_keeps_only_bang_question():
    """逗号/句号/分号/冒号被消费；感叹/问号保留在前句末尾。"""
    text = "有一个经典名场面，在每年高考前后的那段时间总会上演。文科与理科的对立已经成为对立金字塔中的前几名了！"
    parts = _split_sentences(text)
    joined = "".join(parts)
    # 文字一字不漏（去标点后）
    nf = lambda s: s.replace("，", "").replace("。", "").replace("；", "").replace("：", "")
    assert nf(joined) == nf(text)
    # 仅 ！？ 保留
    for p in "，。；：":
        assert p not in joined, f"标点 {p} 不应出现在字幕中"
    assert "！" in joined
    assert parts[-1].endswith("！")


def test_split_verbatim_text_no_consumed_chars():
    """切分不吞文字字符（只消费标点）。"""
    text = "第一句，包含逗号。第二句包含句号！第三句有问号？第四句：有冒号；第五句有分号。"
    parts = _split_sentences(text)
    joined = "".join(parts)
    nf = lambda s: "".join(ch for ch in s if ch not in "，。！；？：")
    assert nf(joined) == nf(text)
    # 结果中只有 ！？ 两种标点
    for ch in joined:
        assert ch not in "，。；："


def test_split_empty_and_plain():
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []
    parts = _split_sentences("没有标点的一段文字")
    assert "".join(parts) == "没有标点的一段文字"


def test_split_question_exclamation_kept():
    """问叹号保留，回归确认。"""
    text = "这样行吗？不行！那怎么办？"
    parts = _split_sentences(text)
    assert "".join(parts) == text  # 仅 ！？ 时拼接与原文一致
    assert parts[0].endswith("？")
    assert parts[1].endswith("！")


def test_split_long_sentence_consumes_comma():
    """长句二次切分：逗号消费（不留在段尾）。"""
    text = ("这是一个非常长的句子，超过了四十个字符，所以会被二次切分，但逗号必须被消费，不能出现在任何字幕段中。")
    parts = _split_sentences(text)
    joined = "".join(parts)
    assert "，" not in joined
    assert "。" not in joined
    nf = lambda s: "".join(ch for ch in s if ch not in "，。！；？：")
    assert nf(joined) == nf(text)
