"""轮71：StructureAgent 系统提示词消费 persona identity 字段。"""

from __future__ import annotations

from clipwright.agents.structure_agent import StructureAgent


def _prompt(identity: dict) -> str:
    return StructureAgent._build_system_prompt(identity, {}, {}, {})


class TestPersonaIdentityWiring:
    def test_positioning_and_class_perspective_in_prompt(self) -> None:
        prompt = _prompt({
            "tone": "专业",
            "positioning": "硬核科技评测",
            "class_perspective": "工薪阶层",
        })
        assert "账号定位: 硬核科技评测" in prompt
        assert "阶层视角: 工薪阶层" in prompt

    def test_absent_fields_produce_no_extra_lines(self) -> None:
        prompt = _prompt({"tone": "轻松"})
        assert "账号定位" not in prompt
        assert "阶层视角" not in prompt

    def test_tone_none_falls_back_to_neutral(self) -> None:
        prompt = _prompt({"tone": None})
        assert "语调: neutral" in prompt

    def test_other_persona_fields_still_interpolated(self) -> None:
        prompt = StructureAgent._build_system_prompt(
            {"tone": "严肃"},
            {"academic_density": 0.6, "max_sentence_len": 24},
            {"cut_profile": "fast_cut"},
            {"max_duration_sec": 600},
        )
        assert "学术密度: 0.6" in prompt
        assert "最长句长: 24 字" in prompt
        assert "剪辑节奏: fast_cut" in prompt
        assert "不超过 600s" in prompt
