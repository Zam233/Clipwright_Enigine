"""P4-4B: 市场包离线安装测试（解包安全 + 结构校验 + 重复安装拒绝）。"""

from __future__ import annotations

import io
import tarfile

import pytest

from clipwright.services.install_service import (
    install_persona_from_bytes,
    install_plugin_from_bytes,
)


def _tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            data = content.encode("utf-8")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


PLUGIN_YAML = """id: unit_test_plugin
name: 单元测试插件
version: 1.0.0
kind: capability
"""

PLUGIN_MAIN = """from clipwright.plugins.base import BasePlugin


class UnitTestPlugin(BasePlugin):
    \"\"\"测试用最小插件。\"\"\"

    def initialize(self) -> None:
        pass
"""


def _plugin_tarball(files: dict[str, str] | None = None) -> bytes:
    merged = {"plugin.yaml": PLUGIN_YAML, "main.py": PLUGIN_MAIN}
    if files:
        merged.update(files)
    return _tarball(merged)

PERSONA_YAML = """persona_id: unit_test_persona
persona_name: 单元测试人格
version: 1.0.0
parameter:
  persona_id: unit_test_persona
  identity:
    tone: neutral
"""


class TestPluginInstall:
    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.plugin_dir", tmp_path / "plugins")
        data = _plugin_tarball({"../evil.py": "x"})
        with pytest.raises(ValueError, match="非法路径"):
            install_plugin_from_bytes(data, "evil_plugin")

    def test_missing_manifest_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.plugin_dir", tmp_path / "plugins")
        data = _tarball({"main.py": "print(1)"})
        with pytest.raises(ValueError, match="缺少必需"):
            install_plugin_from_bytes(data, "no_manifest")

    def test_duplicate_install_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.plugin_dir", tmp_path / "plugins")
        data = _plugin_tarball()
        install_plugin_from_bytes(data, "dup_plugin")
        with pytest.raises(ValueError, match="已安装"):
            install_plugin_from_bytes(data, "dup_plugin")


class TestPersonaInstall:
    def test_valid_persona_installs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.persona_dir", tmp_path / "personas")
        data = _tarball({"persona.yaml": PERSONA_YAML})
        dest = install_persona_from_bytes(data, "unit_test_persona")
        assert (dest / "persona.yaml").exists()

    def test_invalid_persona_yaml_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.persona_dir", tmp_path / "personas")
        data = _tarball({"persona.yaml": "persona_id: [非法结构"})
        with pytest.raises(Exception):
            install_persona_from_bytes(data, "bad_persona")

    def test_duplicate_persona_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.persona_dir", tmp_path / "personas")
        data = _tarball({"persona.yaml": PERSONA_YAML})
        install_persona_from_bytes(data, "dup_persona")
        with pytest.raises(ValueError, match="已存在"):
            install_persona_from_bytes(data, "dup_persona")
