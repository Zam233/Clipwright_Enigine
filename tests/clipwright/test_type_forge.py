"""TypeForge 会话/LLM 草稿测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.services.type_forge import TypeForge


class _LLM:
    async def generate(self, messages=None, **kwargs):
        class R:
            success = True
            content = '{"reply":"已记录","type_draft":{"id":"demo_type","name":"示例类型","prompts":{"genre":{"text":"快速知识解说"}}},"missing_fields":["shot_params"],"progress":{"genre":1}}'
        return R()


@pytest.fixture()
def forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TypeForge:
    monkeypatch.setattr(TypeForge, "SESSIONS_DIR", tmp_path / "typeforge")
    f = TypeForge()
    f._llm = _LLM()
    return f


@pytest.mark.asyncio
async def test_message_and_detail(forge: TypeForge) -> None:
    sid = (await forge.start())["session_id"]
    out = await forge.message(sid, "做知识区快节奏类型")
    assert out["draft"]["id"] == "demo_type"
    detail = forge.detail(sid)
    assert detail is not None and detail["messages"][0]["role"] == "user"
    assert detail["draft"]["prompts"]["genre"]["text"] == "快速知识解说"


@pytest.mark.asyncio
async def test_restart_restores_session(forge: TypeForge) -> None:
    sid = (await forge.start())["session_id"]
    await forge.message(sid, "做知识区快节奏类型")
    again = TypeForge()
    again._llm = forge._llm
    assert again.detail(sid) is not None
