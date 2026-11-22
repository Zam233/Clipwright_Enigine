"""插件 id 重命名后的 PluginData 迁移。"""

from __future__ import annotations

from pathlib import Path

from clipwright.plugins.loader import PluginLoader


def test_image_plugin_data_migrates_from_legacy_id(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    data = tmp_path / "data"
    old = data / "plugins" / "image_providers"
    old.mkdir(parents=True)
    (old / "config.yaml").write_text("api_key: old\n", encoding="utf-8")
    (old / ".enabled").write_text("disabled", encoding="utf-8")

    loader = PluginLoader(plugins, data)
    assert loader.is_enabled("image_generate") is False
    new = loader.get_plugin_data_dir("image_generate")
    assert new.is_dir()
    assert (new / "config.yaml").read_text(encoding="utf-8") == "api_key: old\n"
    assert (new / ".enabled").read_text(encoding="utf-8") == "disabled"
    # 旧目录保留，不破坏回滚
    assert old.is_dir()


def test_no_legacy_dir_creates_empty_data_dir(tmp_path) -> None:
    loader = PluginLoader(tmp_path / "plugins", tmp_path / "data")
    d = loader.get_plugin_data_dir("image_generate")
    assert d.is_dir()
    assert not any(d.iterdir())
