"""长文稿分镜：段落边界由代码定，模型只做逐段标注（E2E 2026-10-28）。

现象：让模型「一次性输出 43 个场景（每条带 200 字口播原话）」的 JSON，
即使给到 16384 预算，它也只吐出散文前言就被截断——
日志实证 `LLM 响应长度=5599` 但 `解析出 0 个原始场景`，
于是整条链路退化成模板分镜 / 逐段兜底。

修复后的契约（`_annotate_segments`）：
1. 段落切分是确定性的（代码算），模型只写 title/description/keywords；
2. 模型漏段 → 该段用文稿前缀做标题补上，**覆盖度仍然是 100%**；
3. 口播原话由代码贴回，时长按字数比例分到配音总长；
4. 模型完全没给出可用结构 → 返回 []，由上层走原路径（不静默吞掉）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents.structure_agent import StructureAgent
from clipwright.schema.agent import AgentContext

SEGMENTS = [f"第{i}段口播内容，用于验证逐段标注。" for i in range(6)]


def _ctx() -> AgentContext:
    return AgentContext(pipeline_id="p1", persona_id="", category_plugin_id="knowledge_longform",
                        topic="t", extra_params={})


class _LLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    async def structured_output(self, system_prompt="", user_prompt="",  # noqa: ANN001, ANN003
                                output_schema=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.calls.append({"user_prompt": user_prompt, "max_tokens": kwargs.get("max_tokens")})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _svc(llm: _LLM) -> StructureAgent:
    agent = StructureAgent.__new__(StructureAgent)
    agent._llm = llm  # noqa: SLF001
    agent.timeout_sec = 30
    return agent


def test_every_segment_becomes_a_scene_with_verbatim_voiceover():
    llm = _LLM({"scenes": [
        {"index": i, "title": f"标题{i}", "description": f"画面{i} [文字动画]",
         "keywords": [f"k{i}"]} for i in range(6)
    ]})
    scenes = asyncio.run(_svc(llm)._annotate_segments(  # noqa: SLF001
        SEGMENTS, "sys", _ctx(), 600.0,
    ))

    assert len(scenes) == 6
    assert [s["voiceover_script"] for s in scenes] == SEGMENTS, "口播必须是原话，逐段对齐"
    assert sum(s["duration_sec"] for s in scenes) == pytest.approx(600.0, abs=0.5)
    assert all(s["title"] and s["description"] and s["keywords"] for s in scenes)
    assert llm.calls[0]["max_tokens"] and llm.calls[0]["max_tokens"] >= 4096
    assert "6 段" in llm.calls[0]["user_prompt"], "prompt 要明确段数，避免模型自行合并"


def test_missing_segments_are_filled_so_coverage_stays_complete():
    """模型只给了 2 段 → 其余段落仍要有场景（覆盖度不靠兜底分支保证）。"""
    llm = _LLM({"scenes": [
        {"index": 0, "title": "开头", "description": "画面 [文字动画]", "keywords": ["a"]},
        {"index": 1, "title": "承接", "description": "画面 [文字动画]", "keywords": ["b"]},
    ]})
    scenes = asyncio.run(_svc(llm)._annotate_segments(SEGMENTS, "sys", _ctx(), 60.0))  # noqa: SLF001

    assert len(scenes) == len(SEGMENTS)
    assert scenes[2]["voiceover_script"] == SEGMENTS[2]
    assert scenes[2]["title"].startswith(SEGMENTS[2][:6])


def test_long_segment_duration_is_proportional():
    segments = ["短" * 10, "长" * 90]
    llm = _LLM({"scenes": [
        {"index": 0, "title": "a", "description": "x [文字动画]", "keywords": ["a"]},
        {"index": 1, "title": "b", "description": "y [文字动画]", "keywords": ["b"]},
    ]})
    scenes = asyncio.run(_svc(llm)._annotate_segments(segments, "sys", _ctx(), 100.0))  # noqa: SLF001

    assert scenes[0]["duration_sec"] == pytest.approx(10.0, abs=0.5)
    assert scenes[1]["duration_sec"] == pytest.approx(90.0, abs=0.5)


def test_llm_failure_returns_empty_for_upstream_fallback():
    llm = _LLM(RuntimeError("boom"))
    assert asyncio.run(_svc(llm)._annotate_segments(SEGMENTS, "sys", _ctx(), 60.0)) == []  # noqa: SLF001


def test_useless_payload_returns_empty():
    llm = _LLM({"scenes": []})
    assert asyncio.run(_svc(llm)._annotate_segments(SEGMENTS, "sys", _ctx(), 60.0)) == []  # noqa: SLF001
