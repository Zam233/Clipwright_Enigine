"""Persona 系统测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from clipwright.persona.loader import load_persona_manifest
from clipwright.persona.validator import validate_manifest
from clipwright.schema.persona import AudioConfig


class TestPersonaLoader:
    @staticmethod
    def _write_minimal_persona(root: Path) -> Path:
        (root / "persona.yaml").write_text(
            "persona_id: sample_persona\npersona_name: Sample Persona\n",
            encoding="utf-8",
        )
        (root / "parameter.yaml").write_text(
            "persona_id: sample_persona\n", encoding="utf-8"
        )
        return root

    def test_load_minimal_persona(self, tmp_path: Path) -> None:
        persona_dir = self._write_minimal_persona(tmp_path)
        manifest = load_persona_manifest(persona_dir)
        assert manifest.persona_id == "sample_persona"
        assert manifest.persona_name == "Sample Persona"
        assert manifest.parameter is not None

    def test_validate_minimal_persona(self, tmp_path: Path) -> None:
        persona_dir = self._write_minimal_persona(tmp_path)
        manifest = load_persona_manifest(persona_dir)
        warnings = validate_manifest(manifest)
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
