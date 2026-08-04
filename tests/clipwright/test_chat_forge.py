"""ChatForge 单元测试：prompt 编译（含截断修复回归）。"""

from __future__ import annotations

import pytest

from clipwright.services.chat_forge import ChatForge, ChatForgeSession


def _long_style_message(chars: int = 2000) -> str:
    """构造一段 >500 字、内容可预期的风格描述。"""
    return "我偏好冷峻克制的叙述节奏，" + "用词精准，杜绝冗余铺垫，" * 40 + "尾句收束有力。" * 20


class TestBuildPromptFromSession:
    """_build_prompt_from_session 测试。"""

    def test_long_message_not_truncated(self) -> None:
        """>500 字用户消息应完整保留，不截断。"""
        content = _long_style_message()
        assert len(content) > 500
        messages = [{"role": "user", "content": content}]
        prompt = ChatForge._build_prompt_from_session(messages)
        assert content in prompt
        assert len(prompt) > 500
        # 消息末尾子串也应存在（此前 [:500] 会截掉）
        assert content[-40:] in prompt

    def test_tail_substring_of_long_message_present(self) -> None:
        """长消息靠后的唯一子串应完整出现在 prompt 中。"""
        tail = "这段收尾独特标记 TAIL_42_END"
        head = "前缀风格描述，"
        content = head + "填充内容，" * 150 + tail
        assert len(content) > 500
        prompt = ChatForge._build_prompt_from_session([{"role": "user", "content": content}])
        assert tail in prompt

    def test_mixed_messages_system_excluded_full_content(self) -> None:
        """多条消息：[系统] 前缀被排除，普通用户消息完整保留、不截断。"""
        normal = "我喜欢高密度信息流，" + "每句都包含实质论点，" * 60 + "结尾标记 SYS_MIX_OK"
        assert len(normal) > 500
        messages = [
            {"role": "assistant", "content": "收到，继续描述。"},
            {"role": "user", "content": "[系统] 用户上传了参考文档「风格稿」（3000 字）。"},
            {"role": "user", "content": normal},
            {"role": "user", "content": "[系统] 第 2/3 章：节奏"},  # 未附正文也应排除
        ]
        prompt = ChatForge._build_prompt_from_session(messages)
        assert "[系统]" not in prompt
        assert normal in prompt
        assert normal[-40:] in prompt
        assert "SYS_MIX_OK" in prompt

    def test_empty_session_returns_empty(self) -> None:
        """空会话返回空串，不崩溃。"""
        assert ChatForge._build_prompt_from_session([]) == ""

    def test_empty_and_whitespace_user_content(self) -> None:
        """空/纯空白用户内容不崩溃。"""
        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "正常描述"},
        ]
        prompt = ChatForge._build_prompt_from_session(messages)
        assert "用户风格描述" in prompt
        assert "正常描述" in prompt

    def test_prompt_compile_structure_preserved(self) -> None:
        """编译结构（header / 用户风格描述 / 说明）保持不变。"""
        messages = [{"role": "user", "content": "我走吐槽流，节奏快。"}]
        prompt = ChatForge._build_prompt_from_session(messages)
        assert "# ChatForge 对话生成的 Persona Prompt" in prompt
        assert "## 用户风格描述" in prompt
        assert "## 说明" in prompt
        assert prompt.index("# ChatForge") < prompt.index("## 用户风格描述")
        assert prompt.index("## 用户风格描述") < prompt.index("## 说明")


class TestChatForgeSessionExpiry:
    """会话过期标记测试。"""

    def test_is_expired_property(self) -> None:
        """is_expired 属性可用（1h 内存过期保留）。"""
        import datetime

        session = ChatForgeSession(session_id="test_exp")
        session.updated_at = datetime.datetime.now() - datetime.timedelta(hours=2)
        assert session.is_expired is True
        session.updated_at = datetime.datetime.now()
        assert session.is_expired is False
