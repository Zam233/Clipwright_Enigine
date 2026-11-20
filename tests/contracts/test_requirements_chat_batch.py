"""B16：需求聊天与规划同批 —— 会话摘要进入生成上下文的纯函数。"""

from __future__ import annotations

from clipwright.services.requirements_service import chat_batch_digest


def test_digest_keeps_user_and_assistant_text_only():
    messages = [
        {"role": "system", "content": "系统提示不应出现"},
        {"role": "user", "content": "做一期 60 秒的数码评测"},
        {"role": "assistant", "content": "好的，用横屏 + 大字幕"},
        {"role": "tool", "content": "list_dir -> [...]"},
        {"role": "user", "content": {"name": "卡片"}},
        {"role": "assistant", "content": "  语气  要克制  "},
    ]
    digest = chat_batch_digest(messages)
    assert "用户：做一期 60 秒的数码评测" in digest
    assert "助手：好的，用横屏 + 大字幕" in digest
    assert "助手：语气 要克制" in digest
    assert "系统提示" not in digest and "list_dir" not in digest
    assert "卡片" not in digest


def test_digest_limit_and_truncation():
    messages = [{"role": "user", "content": f"第{i}条" + "长" * 300} for i in range(20)]
    digest = chat_batch_digest(messages, limit=2, max_chars=10000)
    assert "第18条" in digest and "第19条" in digest
    assert "第15条" not in digest and "第0条" not in digest
    truncated = chat_batch_digest(messages, limit=2, max_chars=120)
    assert len(truncated) == 120


def test_digest_empty_when_no_chat():
    assert chat_batch_digest([]) == ""
    assert chat_batch_digest(None) == ""
    assert chat_batch_digest([{"role": "tool", "content": "x"}]) == ""
