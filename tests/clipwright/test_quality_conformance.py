"""A-09 接线：质检里的类型同一性条目（PRD F12 / V16 / V17）。

V17 起 ``_check_type_conformance`` 是 async（LLM 裁判必须 await）；本文件用
``asyncio.run`` 驱动，并对裁判分支注入假 LLM（不联网、零成本）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents.quality_agent import _check_type_conformance
from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import SignatureTrait
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class _TraitsPlugin(BaseCategoryPlugin):
    plugin_id = "trait_demo"
    display_name = "Traits"

    def __init__(self, traits: list[SignatureTrait] | None = None) -> None:
        self._traits = traits or []

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_signature_traits(self):  # type: ignore[no-untyped-def]
        return self._traits


class _Ctx:
    def __init__(self, category: str, extra: dict | None = None) -> None:
        self.category_plugin_id = category
        self.extra_params = extra or {}


def _timeline(durations: list[float]) -> Timeline:
    start = 0.0
    clips = []
    for index, duration in enumerate(durations):
        clips.append(
            Clip(
                id=f"c{index}", kind=ClipKind.VIDEO, asset_id=f"a{index}",
                track_id="v", start_sec=start, duration_sec=duration,
                source_offset_sec=0, speed=1, volume=1, opacity=1, keyframes=[],
            )
        )
        start += duration
    return Timeline(
        id="tl", width=1920, height=1080, fps=30, duration_sec=start,
        tracks=[Track(id="v", name="v", kind=ClipKind.VIDEO, index=0, clips=clips)],
    )


def _issues(timeline, ctx):  # type: ignore[no-untyped-def]
    return asyncio.run(_check_type_conformance(timeline, ctx))


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


class TestQualityConformance:
    def test_no_category_is_noop(self) -> None:
        assert _issues(_timeline([2.0]), _Ctx("")) == []

    def test_type_without_traits_is_noop(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        assert _issues(_timeline([2.0]), _Ctx("trait_demo")) == []

    def test_strict_failure_becomes_error(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="strict")])
        )
        issues = _issues(_timeline([1.0]), _Ctx("trait_demo"))
        errors = [i for i in issues if i.severity == "error"]
        assert errors and errors[0].category == "type_conformance"

    def test_soft_failure_becomes_warning(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="soft")])
        )
        issues = _issues(_timeline([1.0]), _Ctx("trait_demo"))
        levels = {i.severity for i in issues}
        assert "warning" in levels and "error" not in levels

    def test_passing_trait_only_reports_judge_skipped(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=1000, enforce="strict")])
        )
        issues = _issues(_timeline([2.0]), _Ctx("trait_demo"))
        assert len(issues) == 1
        assert issues[0].severity == "info"
        assert "未做 LLM 同一性复核" in issues[0].message

    def test_unknown_type_is_noop(self) -> None:
        assert _issues(_timeline([2.0]), _Ctx("missing")) == []

    def test_timeline_none_is_noop(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        assert _issues(None, _Ctx("trait_demo")) == []


class TestJudgeWiring:
    """V17：裁判开启后，"指标全过但形态不对"必须变成 error 且带判据。"""

    def test_mismatch_becomes_error_with_evidence(self, monkeypatch) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=1000, enforce="strict")])
        )

        async def fake_judge(traits, measured, report, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "mismatch": True,
                "reasons": ["满屏文字覆盖 + 单镜头 20 秒，形态是口播切片而非歌词 MV"],
                "evidence": [{"metric": "avg_shot_ms", "value": 20000, "expectation": "≤3200"}],
            }

        monkeypatch.setattr("clipwright.services.genre_judge.llm_judge", fake_judge)
        issues = _issues(_timeline([20.0]), _Ctx("trait_demo", {"enable_genre_judge": True}))
        errors = [i for i in issues if i.severity == "error"]
        assert errors, [i.message for i in issues]
        assert "错配" in errors[0].message
        assert "口播切片" in errors[0].message
        assert "avg_shot_ms=20000" in errors[0].message

    def test_match_keeps_no_error(self, monkeypatch) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=1000, enforce="strict")])
        )

        async def fake_judge(traits, measured, report, **kwargs):  # type: ignore[no-untyped-def]
            return {"mismatch": False, "reasons": ["镜头与节奏符合"]}

        monkeypatch.setattr("clipwright.services.genre_judge.llm_judge", fake_judge)
        issues = _issues(_timeline([2.0]), _Ctx("trait_demo", {"enable_genre_judge": True}))
        assert [i for i in issues if i.severity == "error"] == []
        # 判过时不再输出"未做复核"的 info
        assert [i for i in issues if "未做 LLM 同一性复核" in i.message] == []

    def test_judge_exception_degrades_to_skipped(self, monkeypatch) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=1000, enforce="strict")])
        )

        async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("LLM 挂了")

        monkeypatch.setattr("clipwright.services.genre_judge.llm_judge", boom)
        issues = _issues(_timeline([2.0]), _Ctx("trait_demo", {"enable_genre_judge": True}))
        info = [i for i in issues if i.severity == "info"]
        assert info and "未做 LLM 同一性复核" in info[0].message
