"""规划书翻译的两段式调用（E2E 2026-10-28 的降级链）。

现象：702s 配音的知识区长片，`plan_translate` 的 prompt 里塞了 8000 字原文 +
简报 + 联网参考；推理型模型（deepseek-v4-pro）在这份输入上反复"只产出 reasoning、
正文为空"（HTTP 200 + content=""），三次重试全空 + 420s 超时 → 规划书退化成
确定性兜底表（「使用基础版本」），用户看到的就是一张干表。

修复后的契约：
1. 先用完整 prompt（大预算）试；
2. 没有正文 → **降规模重试一次**：原文砍到 2500 字、去掉联网参考；
3. 仍然没有 → 才用确定性 `_default_markdown` 兜底（不挂起、不谎称成功）。
"""

from __future__ import annotations

import pytest

from clipwright.services import requirements_service as rs

SCRIPT = "第一段原文。" * 2000  # 远超 2500 字的原文


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.success = True
        self.status_code = 200


class _FakeLLM:
    """第一次调用返回空 dict（模拟"只推理没正文"），第二次返回正文。"""

    def __init__(self, results: list[dict]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def structured_output(self, system_prompt="", user_prompt="",  # noqa: ANN001, ANN003
                                output_schema=None, use_flash=False, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "max_tokens": kwargs.get("max_tokens"),
        })
        return self.results.pop(0) if self.results else {}


def _svc(llm: _FakeLLM) -> rs.RequirementsService:
    svc = rs.RequirementsService.__new__(rs.RequirementsService)
    svc._llm = llm  # noqa: SLF001
    return svc


SCENES = [{"title": "s0", "duration_sec": 100, "voiceover_script": "v0",
           "visual_description": "d0"}]


@pytest.mark.asyncio
async def test_empty_first_attempt_retries_with_compact_prompt(monkeypatch):
    # 把内层重试压到 1 次：本用例测的是"外层两段式"，不是内层重试计数
    monkeypatch.setattr(rs, "LLM_RETRY_MAX", 1)
    llm = _FakeLLM([{}, {"markdown_content": "# 规划书\n\n正文", "summary": "ok"}])
    svc = _svc(llm)

    plan = await svc._translate_plan(SCENES, {"title": "t"}, SCRIPT, web_context="网搜")

    assert len(llm.calls) == 2, "空正文必须触发一次精简重试"
    first, second = llm.calls
    assert first["max_tokens"] == rs.PLAN_TRANSLATE_MAX_TOKENS
    assert "联网搜索参考" in first["system_prompt"]
    assert "联网搜索参考" not in second["system_prompt"], "精简重试要丢掉联网参考"
    # 原文**两次都要灌注**（用户明确要求：不灌原文会影响规划书质量）
    assert "原始文稿" in first["system_prompt"] and "原始文稿" in second["system_prompt"]
    assert SCRIPT[:800] in second["system_prompt"], "精简重试也不能砍原文"
    assert plan["markdown_content"].startswith("# 规划书\n\n正文"), "LLM 正文要在最前面"
    assert "视频成片规划书" in plan["markdown_content"], "完整镜头表统一追加在文末"
    assert plan["raw_scenes"] == SCENES


@pytest.mark.asyncio
async def test_both_attempts_empty_fall_back_to_default_markdown(monkeypatch):
    monkeypatch.setattr(rs, "LLM_RETRY_MAX", 1)
    llm = _FakeLLM([{}, {}])
    svc = _svc(llm)

    plan = await svc._translate_plan(SCENES, None, "")

    assert len(llm.calls) == 2
    assert "视频成片规划书" in plan["markdown_content"], "兜底仍然是可读的确定性表格"
    assert plan["scene_count"] == 1
    assert plan["total_duration_sec"] == 100
