"""V50 夹具脚本的自测：缺类 → 退出码 1；四类齐全 → 退出码 0 且各类自洽。

V50 的**片源**必须由人提供（见 `examples/material_samples/README.md`），本测试
用一个临时目录里的空占位文件验证脚本本身的判定逻辑（不产出、不冒充真实素材）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_v50_material_samples.py"

FIXTURES = {
    "hand_drawn": "hand_drawn_ink_sketch_city.png",
    "2d": "2d_flat_cartoon_animation.png",
    "3d": "3d_render_cgi_shader.png",
    "live_action": "live_action_street_footage.mp4",
}


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_v50", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_tree(root: Path, classes: list[str]) -> None:
    for key in classes:
        folder = root / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / FIXTURES[key]).write_bytes(b"")


def test_missing_classes_fail(tmp_path, capsys) -> None:
    module = _load()
    _make_tree(tmp_path, ["hand_drawn", "2d", "3d"])  # 缺实拍
    code = module.main(["--dir", str(tmp_path), "--out", str(tmp_path / "out")])
    assert code == 1
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["missing_classes"] == ["实拍"]
    assert report["ok"] is False
    assert "补齐" in capsys.readouterr().out


def test_all_four_classes_pass(tmp_path) -> None:
    module = _load()
    _make_tree(tmp_path, list(FIXTURES))
    code = module.main(["--dir", str(tmp_path), "--out", str(tmp_path / "out")])
    assert code == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["indexed_files"] == 4
    for item in report["classes"]:
        assert item["ok"] is True, item
        assert item["own_hits"], item
    assert report["missing_classes"] == []


def test_unknown_extension_ignored(tmp_path) -> None:
    module = _load()
    _make_tree(tmp_path, list(FIXTURES))
    (tmp_path / "hand_drawn" / "notes.txt").write_text("x", encoding="utf-8")
    assert module.main(["--dir", str(tmp_path), "--out", str(tmp_path / "out")]) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    hand = next(item for item in report["classes"] if item["class"] == "hand_drawn")
    assert "notes.txt" not in hand["files"]


@pytest.mark.parametrize("key", list(FIXTURES))
def test_each_class_hits_its_own_file(tmp_path, key) -> None:
    module = _load()
    _make_tree(tmp_path, list(FIXTURES))
    module.main(["--dir", str(tmp_path), "--out", str(tmp_path / "out")])
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    item = next(entry for entry in report["classes"] if entry["class"] == key)
    assert FIXTURES[key] in item["own_hits"], item
