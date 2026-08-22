"""Phase 4.3 回归：Persona 多层继承 + 循环防御。"""

from __future__ import annotations

import pytest

from clipwright.persona.loader import PersonaLoadError, load_persona_by_id, resolve_inheritance
from clipwright.schema.persona import ParameterLayer, PersonaManifest


def _mk(persona_id: str, tone: str | None = None, inherits: str = "") -> PersonaManifest:
    param = ParameterLayer(persona_id=persona_id) if tone else None
    return PersonaManifest(
        persona_id=persona_id,
        persona_name=persona_id,
        inherits=inherits,
        parameter=param,
    )


def _write(tmp_path, manifest: PersonaManifest) -> None:
    import yaml
    d = tmp_path / manifest.persona_id
    d.mkdir(parents=True, exist_ok=True)
    data = manifest.model_dump(mode="json")
    (d / "persona.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_multi_level_inheritance(tmp_path, monkeypatch):
    """祖父 → 父 → 子：子缺 parameter，父也缺 → 取祖父的值。"""
    from clipwright.config import settings

    monkeypatch.setattr(settings, "persona_dir", tmp_path)
    _write(tmp_path, _mk("grand", tone="冷静"))
    _write(tmp_path, _mk("parent", inherits="grand"))
    _write(tmp_path, _mk("child", inherits="parent"))

    child_loaded = load_persona_by_id("child", tmp_path)
    resolved = resolve_inheritance(child_loaded)
    assert resolved.parameter is not None
    assert resolved.parameter.persona_id == "grand"


def test_cycle_detection(tmp_path, monkeypatch):
    """循环继承（A→B→A）抛 PersonaLoadError。"""
    from clipwright.config import settings

    monkeypatch.setattr(settings, "persona_dir", tmp_path)
    _write(tmp_path, _mk("a", inherits="b"))
    _write(tmp_path, _mk("b", inherits="a"))
    a_loaded = load_persona_by_id("a", tmp_path)
    with pytest.raises(PersonaLoadError):
        resolve_inheritance(a_loaded)


def test_no_inherit_returns_self():
    m = _mk("solo", tone="直接")
    assert resolve_inheritance(m) is m