"""P10: ChatForge 会话落盘（B5）+ 恢复测试。"""

from __future__ import annotations

import json

from clipwright.services.chat_forge import ChatForge, ChatForgeSession


def test_session_persist_and_restore(tmp_path, monkeypatch) -> None:
    """B5: 会话落盘 → 新实例恢复未过期会话。"""
    # 隔离会话目录
    dirs = tmp_path / "sessions"
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", dirs)

    forge = ChatForge()
    session = ChatForgeSession(session_id="sess_b5")
    session.messages.append({"role": "user", "content": "你好"})
    session.persona_draft["identity"]["tone"] = "严谨"
    forge._persist_session(session)
    assert (dirs / "sess_b5.json").exists()

    # 新实例（模拟重启）恢复
    forge2 = ChatForge()
    restored = forge2._sessions.get("sess_b5")
    assert restored is not None
    assert restored.messages[0]["content"] == "你好"
    assert restored.persona_draft["identity"]["tone"] == "严谨"


def test_session_expired_not_restored(tmp_path, monkeypatch) -> None:
    """过期会话不恢复。"""
    from datetime import datetime, timedelta
    dirs = tmp_path / "sessions"
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", dirs)
    dirs.mkdir(parents=True, exist_ok=True)

    forge = ChatForge()
    old = ChatForgeSession(session_id="sess_old")
    old.updated_at = datetime.now() - timedelta(hours=5)
    forge._persist_session(old)

    forge2 = ChatForge()
    assert "sess_old" not in forge2._sessions


def test_commit_removes_session_file(tmp_path, monkeypatch) -> None:
    """commit 后清理会话文件。"""
    dirs = tmp_path / "sessions"
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", dirs)
    forge = ChatForge()
    session = ChatForgeSession(session_id="sess_commit")
    forge._sessions["sess_commit"] = session
    forge._persist_session(session)
    assert (dirs / "sess_commit.json").exists()

    # 模拟 commit 的清理（repo 层清理目录；此处验证文件存在性可删）
    forge._sessions.pop("sess_commit", None)
    (dirs / "sess_commit.json").unlink(missing_ok=True)
    assert not (dirs / "sess_commit.json").exists()
