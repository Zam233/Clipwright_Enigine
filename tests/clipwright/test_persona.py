"""Persona 系统测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from clipwright.persona.loader import load_persona_manifest
from clipwright.persona.validator import validate_manifest
from clipwright.schema.persona import AudioConfig


class TestPersonaLoader:
    def test_load_sample_persona(self) -> None:
        persona_dir = Path("personas/zam_knowledge_critical")
        if not persona_dir.exists():
            pytest.skip("Sample persona not found (scaffold only)")

        manifest = load_persona_manifest(persona_dir)
        assert manifest.persona_id == "zam_knowledge_critical"
        assert manifest.parameter is not None
        assert manifest.parameter.identity.tone == "critical_intellectual"

    def test_validate_persona(self) -> None:
        persona_dir = Path("personas/zam_knowledge_critical")
        if not persona_dir.exists():
            pytest.skip("Sample persona not found")

        manifest = load_persona_manifest(persona_dir)
        warnings = validate_manifest(manifest)
        # 验证通过应该没有 error 级别的警告
        critical = [w for w in warnings if "required" in w.lower()]
        assert len(critical) == 0


class TestAudioConfig:
    """AudioConfig 字幕烧录开关 (subtitle_enabled) 测试。"""

    def test_subtitle_enabled_defaults_true(self) -> None:
        cfg = AudioConfig()
        assert cfg.subtitle_enabled is True

    def test_subtitle_enabled_explicit_false(self) -> None:
        cfg = AudioConfig(subtitle_enabled=False)
        assert cfg.subtitle_enabled is False

    def test_subtitle_enabled_rejects_non_bool_string(self) -> None:
        with pytest.raises(ValidationError):
            AudioConfig(subtitle_enabled="yes")
