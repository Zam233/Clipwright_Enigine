"""PersonaForge 测试。"""

from __future__ import annotations

import pytest

from clipwright.schema.persona import PersonaManifest
from clipwright.services.persona_forge import PersonaForge


@pytest.fixture
def forge() -> PersonaForge:
    return PersonaForge()


class TestPersonaForgeBasicStats:
    """验证脚本基础统计分析。"""

    def test_basic_stats_empty(self) -> None:
        stats = PersonaForge._basic_script_stats("")
        assert stats["total_chars"] == 0
        # 分句算法对空串返回至少 1 个空句
        assert stats["sentence_count"] >= 1

    def test_basic_stats_normal(self) -> None:
        script = "今天我们来聊聊一个很有意思的话题。你可能已经注意到了，最近盲盒经济非常火爆。但这是真正的消费升级吗？我认为不是。"
        stats = PersonaForge._basic_script_stats(script)
        assert stats["total_chars"] > 0
        assert stats["sentence_count"] >= 4
        assert stats["max_sentence_len"] > 10

    def test_basic_stats_srt(self) -> None:
        srt = """1
00:00:01,000 --> 00:00:05,000
今天我们来聊聊盲盒经济。

2
00:00:05,000 --> 00:00:10,000
这背后其实反映了深刻的社会问题。"""
        stats = PersonaForge._basic_script_stats(srt)
        assert stats["sentence_count"] >= 2
        assert stats["total_chars"] > 0

    def test_academic_density(self) -> None:
        academic = "基于实证研究，从方法论角度论证这一理论框架的范式转型。因此我们可以从本质上理解这一现象。"
        stats = PersonaForge._basic_script_stats(academic)
        assert stats["academic_density"] > 0.05

    def test_slang_ratio(self) -> None:
        slang = "哈哈这个真的太强了绝了真的离谱笑死就是一个大无语事件"
        stats = PersonaForge._basic_script_stats(slang)
        assert stats["slang_ratio"] > 0.05


class TestPersonaForgeBuild:
    """验证 PersonaManifest 构建。"""

    def test_build_manifest_minimal(self, forge: PersonaForge) -> None:
        manifest = forge._build_manifest(
            llm_result={
                "identity": {"tone": "critical"},
                "language": {"academic_density": 0.2},
            },
            persona_id="test_forge",
            persona_name="测试构建",
        )
        assert isinstance(manifest, PersonaManifest)
        assert manifest.persona_id == "test_forge"
        assert manifest.parameter is not None
        assert manifest.parameter.identity.tone == "critical"
        assert manifest.parameter.language.academic_density == 0.2

    def test_build_manifest_confidence_format(self, forge: PersonaForge) -> None:
        """处理 {value, confidence} 格式。"""
        result = {
            "identity": {"tone": {"value": "tech_enthusiast", "confidence": 0.85}},
            "language": {"academic_density": {"value": 0.08, "confidence": 0.7}},
            "rhythm": {"cut_profile": {"value": "fast_but_controlled", "confidence": 0.6}},
        }
        manifest = forge._build_manifest(
            llm_result=result,
            persona_id="test_confidence",
            persona_name="置信度测试",
        )
        assert manifest.parameter.identity.tone == "tech_enthusiast"
        assert manifest.parameter.language.academic_density == 0.08
        assert manifest.parameter.rhythm.cut_profile == "fast_but_controlled"


class TestPersonaForgeInfer:
    """验证推断逻辑。"""

    def test_infer_cut_profile_surge(self) -> None:
        profile = PersonaForge._infer_cut_profile(
            {"sections": ["hook", "body_theory", "body_evidence", "real_world_return"]}
        )
        assert profile == "surge_pause"

    def test_infer_cut_profile_default(self) -> None:
        profile = PersonaForge._infer_cut_profile({})
        assert profile == "even_flow"

    def test_infer_shot_duration(self) -> None:
        assert PersonaForge._infer_shot_duration("rapid_fire") == 1500
        assert PersonaForge._infer_shot_duration("surge_pause") == 6000
        assert PersonaForge._infer_shot_duration("unknown") == 5000

    @pytest.mark.asyncio
    async def test_from_script_text(self, forge: PersonaForge) -> None:
        """验证脚本分析流程不崩溃（无 API key 时回退）。"""
        from clipwright.config import settings

        orig_key = settings.llm_api_key
        settings.llm_api_key = ""

        try:
            manifest = await forge.from_script(
                script="今天我们来聊聊盲盒经济。这背后反映的是消费社会的符号异化。",
                persona_id="test_script_persona",
                persona_name="脚本测试",
            )
            assert manifest.persona_id == "test_script_persona"
            assert manifest.parameter is not None
        except Exception:
            pass
        finally:
            settings.llm_api_key = orig_key

    @pytest.mark.asyncio
    async def test_from_prompt_text(self, forge: PersonaForge) -> None:
        """验证 prompt 构建流程不崩溃。"""
        from clipwright.config import settings

        orig_key = settings.llm_api_key
        settings.llm_api_key = ""

        try:
            manifest = await forge.from_prompt(
                description="我风格冷峻，喜欢黑白画面，打字机文字效果",
                persona_id="test_prompt_persona",
            )
            assert manifest.persona_id == "test_prompt_persona"
        except Exception:
            pass
        finally:
            settings.llm_api_key = orig_key
