"""V17：类型同一性 LLM 裁判（``services/genre_judge.py``）单测。

全部用假 LLM（不联网、零成本）：覆盖提示构造、返回解析、开关、降级，以及
``/api/type-maker/{id}/genre-check`` 的 async 接线（含 payload 开关）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clipwright.services.genre_conformance import acheck_conformance
from clipwright.services.genre_judge import (
    JUDGE_SCHEMA,
    build_user_prompt,
    digest_timeline,
    judge_enabled,
    llm_judge,
    parse_judge_payload,
)
from clipwright.schema.category_spec import SignatureTrait
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class _FakeLLM:
    """最小 LLM 假实现：``structured_output`` 返回预设值并记录入参。"""

    def __init__(self, payload) -> None:  # type: ignore[no-untyped-def]
        self.payload = payload
        self.calls: list[dict] = []
        self.model = "fake-model"

    async def structured_output(self, system_prompt, user_prompt, output_schema=None, **kwargs):
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "schema": output_schema, **kwargs}
        )
        return self.payload


class _ChatOnlyLLM:
    """只实现 chat() 的客户端（结构化输出不可用时的兜底路径）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(messages)
        return self.text


def _timeline(durations: list[float], texts: list[str] | None = None) -> Timeline:
    start = 0.0
    clips = []
    for index, duration in enumerate(durations):
        clips.append(
            Clip(
                id=f"c{index}", kind=ClipKind.VIDEO, asset_id=f"a{index}",
                track_id="v", start_sec=start, duration_sec=duration,
                source_offset_sec=0, speed=1, volume=1, opacity=1, keyframes=[],
                text=(texts[index] if texts and index < len(texts) else None),
            )
        )
        start += duration
    return Timeline(
        id="tl", width=1920, height=1080, fps=30, duration_sec=start,
        tracks=[Track(id="v", name="v", kind=ClipKind.VIDEO, index=0, clips=clips)],
    )


class TestParseJudgePayload:
    def test_plain_json(self) -> None:
        out = parse_judge_payload({"mismatch": True, "reasons": ["a"], "confidence": 0.8})
        assert out["verdict"] == "mismatch"
        assert out["confidence"] == 0.8
        assert out["reasons"] == ["a"]

    def test_fenced_json_string(self) -> None:
        raw = '```json\n{"mismatch": false, "reasons": ["形态符合"]}\n```'
        out = parse_judge_payload(raw)
        assert out["mismatch"] is False
        assert out["verdict"] == "match"

    def test_confidence_clamped(self) -> None:
        assert parse_judge_payload({"mismatch": True, "confidence": 3})["confidence"] == 1.0
        assert parse_judge_payload({"mismatch": True, "confidence": -1})["confidence"] == 0.0
        assert parse_judge_payload({"mismatch": True})["confidence"] == 0.0

    def test_reasons_string_becomes_list(self) -> None:
        assert parse_judge_payload({"mismatch": True, "reasons": "单个理由"})["reasons"] == ["单个理由"]

    def test_evidence_must_be_list_of_dicts(self) -> None:
        out = parse_judge_payload(
            {"mismatch": True, "evidence": [{"metric": "x"}, "junk"]}
        )
        assert out["evidence"] == [{"metric": "x"}]

    def test_missing_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_judge_payload({"reasons": ["没给结论"]})

    def test_unparsable_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_judge_payload("我觉得不太像")


class TestPromptAndDigest:
    def test_prompt_carries_traits_and_measured(self) -> None:
        traits = [SignatureTrait(name="avg_shot_ms", mode="require_le", value=3200, enforce="strict")]
        prompt = build_user_prompt("lyric_mv", traits, {"avg_shot_ms": 20000})
        assert "lyric_mv" in prompt
        assert "avg_shot_ms" in prompt
        assert "20000" in prompt
        # L1 判定要一并给出，裁判才能发现"指标不过"与"指标过了但形态不对"
        assert "不过" in prompt

    def test_prompt_without_traits_is_still_valid(self) -> None:
        assert "未声明指标" in build_user_prompt("x", [], {})

    def test_digest_caps_clips_and_keeps_timing(self) -> None:
        digest = digest_timeline(_timeline([1.0] * 5), max_clips=3)
        assert digest["clip_count"] == 3
        assert digest["duration_sec"] == 5.0
        assert digest["clips"][0]["dur"] == 1.0

    def test_digest_trims_text(self) -> None:
        digest = digest_timeline(_timeline([1.0], ["字" * 200]))
        assert len(digest["clips"][0]["text"]) == 60

    def test_schema_requires_mismatch(self) -> None:
        assert "mismatch" in JUDGE_SCHEMA["required"]


class TestJudgeEnabled:
    def test_default_off(self, monkeypatch) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "genre_judge_enabled", False, raising=False)
        assert judge_enabled({}) is False
        assert judge_enabled(None) is False

    def test_payload_flag_wins(self, monkeypatch) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "genre_judge_enabled", False, raising=False)
        assert judge_enabled({"enable_genre_judge": True}) is True

    def test_explicit_false_beats_server_flag(self, monkeypatch) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "genre_judge_enabled", True, raising=False)
        assert judge_enabled({"enable_genre_judge": False}) is False

    def test_server_flag_used_when_payload_silent(self, monkeypatch) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "genre_judge_enabled", True, raising=False)
        assert judge_enabled({}) is True


class TestLlmJudge:
    def test_structured_output_path(self) -> None:
        llm = _FakeLLM({"mismatch": True, "reasons": ["不像 MV"], "confidence": 0.9})
        traits = [SignatureTrait(name="avg_shot_ms", mode="require_le", value=3200)]
        out = asyncio.run(
            llm_judge(traits, {"avg_shot_ms": 9000}, type_id="lyric_mv", llm=llm)
        )
        assert out["mismatch"] is True
        assert out["judged_by"] == "llm"
        assert out["model"] == "fake-model"
        assert llm.calls and "lyric_mv" in llm.calls[0]["user"]
        assert llm.calls[0]["schema"] is JUDGE_SCHEMA

    def test_chat_only_client_path(self) -> None:
        llm = _ChatOnlyLLM(json.dumps({"mismatch": False, "reasons": ["形态符合"]}))
        out = asyncio.run(llm_judge([], {}, type_id="lyric_mv", llm=llm))
        assert out["mismatch"] is False
        assert llm.calls[0][0]["role"] == "system"

    def test_timeline_digest_included(self) -> None:
        llm = _FakeLLM({"mismatch": False})
        asyncio.run(llm_judge([], {}, type_id="lyric_mv", timeline=_timeline([2.0]), llm=llm))
        assert "clip_count" in llm.calls[0]["user"]

    def test_bad_payload_raises_for_caller_to_degrade(self) -> None:
        llm = _FakeLLM("不是 JSON")
        with pytest.raises(ValueError):
            asyncio.run(llm_judge([], {}, type_id="lyric_mv", llm=llm))


class TestAcheckConformance:
    def _traits(self) -> list[SignatureTrait]:
        return [SignatureTrait(name="avg_shot_ms", mode="require_le", value=3200, enforce="strict")]

    def test_disabled_never_calls_judge(self) -> None:
        called: list[int] = []

        async def judge(*args, **kwargs):  # type: ignore[no-untyped-def]
            called.append(1)
            return {"mismatch": True}

        report = asyncio.run(
            acheck_conformance(self._traits(), {"avg_shot_ms": 100}, enable_genre_judge=False, judge=judge)
        )
        assert report.judge == "skipped"
        assert called == []

    def test_mismatch_marks_report_not_ok(self) -> None:
        async def judge(*args, **kwargs):  # type: ignore[no-untyped-def]
            return {"mismatch": True, "reasons": ["不是该类型"]}

        report = asyncio.run(
            acheck_conformance(self._traits(), {"avg_shot_ms": 100}, enable_genre_judge=True, judge=judge)
        )
        assert report.judge == "mismatch"
        assert report.ok is False
        assert "不是该类型" in report.mismatches

    def test_no_callback_is_skipped(self) -> None:
        report = asyncio.run(
            acheck_conformance(self._traits(), {"avg_shot_ms": 100}, enable_genre_judge=True)
        )
        assert report.judge == "skipped"
        assert "未注入" in report.judge_detail["reason"]

    def test_exception_degrades(self) -> None:
        async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("网络挂")

        report = asyncio.run(
            acheck_conformance(self._traits(), {"avg_shot_ms": 100}, enable_genre_judge=True, judge=boom)
        )
        assert report.judge == "skipped"
        assert "网络挂" in report.judge_detail["reason"]

    def test_match_keeps_ok(self) -> None:
        async def judge(*args, **kwargs):  # type: ignore[no-untyped-def]
            return {"mismatch": False}

        report = asyncio.run(
            acheck_conformance(self._traits(), {"avg_shot_ms": 100}, enable_genre_judge=True, judge=judge)
        )
        assert report.judge == "passed"
        assert report.ok is True


class TestGenreCheckEndpointAsync:
    def test_payload_switch_runs_judge(self, monkeypatch) -> None:
        from clipwright.api.type_maker import run_genre_check_async
        from clipwright.category.base import BaseCategoryPlugin
        from clipwright.category.registry import CategoryRegistry

        class _Plugin(BaseCategoryPlugin):
            plugin_id = "gj_demo"
            display_name = "GJ"

            def translate_persona(self, params):  # type: ignore[no-untyped-def]
                return {}

            def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
                return {}

            def get_signature_traits(self):  # type: ignore[no-untyped-def]
                return [SignatureTrait(name="avg_shot_ms", mode="require_le", value=3200, enforce="strict")]

        CategoryRegistry.clear()
        CategoryRegistry.register(_Plugin())
        try:
            llm = _FakeLLM({"mismatch": True, "reasons": ["形态是口播切片"], "confidence": 0.7})
            result = asyncio.run(
                run_genre_check_async(
                    "gj_demo",
                    {
                        "timeline": json.loads(_timeline([2.0, 2.0]).model_dump_json()),
                        "enable_genre_judge": True,
                    },
                    llm=llm,
                )
            )
            assert result["judge"] == "mismatch"
            assert result["ok"] is False
            assert any("口播切片" in m for m in result["mismatches"])
            assert result["judge_detail"]["confidence"] == 0.7
            assert llm.calls
        finally:
            CategoryRegistry.clear()

    def test_default_payload_skips_judge(self, monkeypatch) -> None:
        from clipwright.api.type_maker import run_genre_check_async
        from clipwright.category.base import BaseCategoryPlugin
        from clipwright.category.registry import CategoryRegistry
        from clipwright.config import settings

        class _Plugin(BaseCategoryPlugin):
            plugin_id = "gj_demo2"
            display_name = "GJ2"

            def translate_persona(self, params):  # type: ignore[no-untyped-def]
                return {}

            def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
                return {}

            def get_signature_traits(self):  # type: ignore[no-untyped-def]
                return [SignatureTrait(name="avg_shot_ms", mode="require_le", value=3200, enforce="strict")]

        CategoryRegistry.clear()
        CategoryRegistry.register(_Plugin())
        monkeypatch.setattr(settings, "genre_judge_enabled", False, raising=False)
        try:
            result = asyncio.run(
                run_genre_check_async(
                    "gj_demo2",
                    {"timeline": json.loads(_timeline([2.0]).model_dump_json())},
                )
            )
            assert result["judge"] == "skipped"
            assert result["ok"] is True
        finally:
            CategoryRegistry.clear()
