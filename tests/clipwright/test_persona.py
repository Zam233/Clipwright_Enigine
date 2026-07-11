"""Persona 系统测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.persona.loader import load_persona_manifest
from clipwright.persona.validator import validate_manifest


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
