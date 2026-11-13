"""shortform_category 的 PRE_RENDER 只作用显式选中的类型。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_plugin_module():
    path = Path(__file__).resolve().parents[2] / "plugins" / "shortform_category" / "main.py"
    spec = importlib.util.spec_from_file_location("shortform_category_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin_module().ShortformCategoryPlugin()


def test_missing_category_keeps_landscape(plugin):
    ctx = {"task_id": "t1", "settings": {"width": 1920, "height": 1080, "fps": 30}}
    result = plugin._inject_vertical(dict(ctx))
    assert result == {}
    assert ctx["settings"] == {"width": 1920, "height": 1080, "fps": 30}


def test_other_category_keeps_landscape(plugin):
    ctx = {"category_id": "gaming_category", "settings": {"width": 1920, "height": 1080}}
    assert plugin._inject_vertical(ctx) == {}
    assert ctx["settings"]["width"] == 1920


def test_shortform_category_applies_vertical(plugin):
    ctx = {"category_id": "shortform_category", "settings": {"width": 1920, "height": 1080}}
    result = plugin._inject_vertical(ctx)
    assert result["settings"]["width"] == 1080
    assert result["settings"]["height"] == 1920


def test_shortform_requires_mapping_settings(plugin):
    ctx = {"category_id": "shortform_category", "settings": "not-a-dict"}
    assert plugin._inject_vertical(ctx) == {}
