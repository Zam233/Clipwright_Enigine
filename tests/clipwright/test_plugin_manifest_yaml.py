"""A1：Loader 与 worker 共用的严格 manifest YAML 解析。"""

from __future__ import annotations

import pytest

from clipwright.plugins.manifest_yaml import PluginManifestError, load_manifest_dict


def test_simple_mapping_is_parsed():
    data = load_manifest_dict('id: demo\napi_version: "2"\ncontributions: []\n')
    assert data["id"] == "demo"
    assert data["api_version"] == "2"


def test_duplicate_top_level_key_is_rejected():
    text = 'id: demo\napi_version: "1"\napi_version: "2"\n'
    with pytest.raises(PluginManifestError, match="api_version"):
        load_manifest_dict(text, source="plugins/demo/plugin.yaml")


def test_duplicate_nested_key_is_rejected():
    text = "contributions:\n  - kind: tool\n    id: x\n    id: y\n"
    with pytest.raises(PluginManifestError, match="contributions\\[0\\]\\.id"):
        load_manifest_dict(text)


def test_non_mapping_root_is_rejected():
    with pytest.raises(PluginManifestError, match="mapping"):
        load_manifest_dict("- a\n- b\n")


def test_empty_document_returns_empty_dict():
    assert load_manifest_dict("") == {}


def test_merge_keys_are_not_treated_as_duplicates():
    text = "base: &b\n  a: 1\nmerged:\n  <<: *b\n  a: 2\n"
    data = load_manifest_dict(text)
    assert data["merged"]["a"] == 2
