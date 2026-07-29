"""llm_mg Storage 层单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plugins.llm_mg.storage import MGStorage


class TestMGStorage:
    """MGStorage 核心逻辑。"""

    def test_save_and_load_generation(self) -> None:
        """保存生成结果并重新加载。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            r = s.save_generation({"animation_id": "test_anim", "elements": []})
            gid = r["generation_id"]
            assert gid.startswith("gen_")

            record = s.load_generation(gid)
            assert record is not None
            assert record["mg_def"]["animation_id"] == "test_anim"

    def test_save_and_load_with_custom_id(self) -> None:
        """使用自定义 generation_id。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            r = s.save_generation({"animation_id": "x"}, generation_id="my_gen_001")
            assert r["generation_id"] == "my_gen_001"
            assert s.load_generation("my_gen_001") is not None

    def test_load_nonexistent(self) -> None:
        """加载不存在的记录返回 None。"""
        s = MGStorage()
        assert s.load_generation("nonexistent_id") is None

    def test_save_as_template(self) -> None:
        """保存生成结果为模板。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            s.save_generation(
                {"animation_id": "mg_my_anim", "name": "My Anim", "elements": []},
                generation_id="gen_x",
            )
            path = s.save_as_template("gen_x", "自定义名称")
            assert "mg_my_anim" in path
            # 生成记录应被清理
            assert s.load_generation("gen_x") is None
            # 模板应可加载
            t = s.load_template("mg_my_anim")
            assert t is not None
            assert t["name"] == "自定义名称"

    def test_save_as_template_id_conflict(self) -> None:
        """模板 ID 冲突时自动追加后缀。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            # 先存一个同名模板
            import json
            (Path(tmp) / "templates" / "mg_conflict.json").write_text(
                json.dumps({"animation_id": "mg_conflict", "elements": []}),
                encoding="utf-8",
            )
            s.save_generation(
                {"animation_id": "mg_conflict", "elements": []},
                generation_id="gen_c",
            )
            path = s.save_as_template("gen_c")
            # 新模板应包含随机后缀
            assert "mg_conflict_" in Path(path).stem

    def test_save_as_template_nonexistent(self) -> None:
        """保存不存在的记录应抛出异常。"""
        import pytest
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            with pytest.raises(FileNotFoundError):
                s.save_as_template("nonexistent_gen")

    def test_get_templates(self) -> None:
        """列出模板元信息。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            # 写入一个模板
            import json
            (Path(tmp) / "templates" / "test_t.json").write_text(
                json.dumps({
                    "animation_id": "test_t",
                    "name": "Test",
                    "description": "desc",
                    "duration_sec": 2.0,
                    "params": {"text": {"type": "string"}},
                }),
                encoding="utf-8",
            )
            templates = s.get_templates()
            assert len(templates) == 1
            t = templates[0]
            assert t["animation_id"] == "test_t"
            assert t["name"] == "Test"
            assert t["duration_sec"] == 2.0
            assert "text" in t["params"]

    def test_get_template_ids(self) -> None:
        """获取模板 ID 列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            import json
            (Path(tmp) / "templates" / "a.json").write_text(
                json.dumps({"animation_id": "id_a", "elements": []}), encoding="utf-8")
            (Path(tmp) / "templates" / "b.json").write_text(
                json.dumps({"animation_id": "id_b", "elements": []}), encoding="utf-8")
            ids = s.get_template_ids()
            assert "id_a" in ids
            assert "id_b" in ids

    def test_list_generations(self) -> None:
        """列出未保存的生成记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            s = MGStorage(Path(tmp))
            s.save_generation({"animation_id": "g1", "name": "Gen 1"}, generation_id="g1")
            s.save_generation({"animation_id": "g2", "name": "Gen 2"}, generation_id="g2")
            gens = s.list_generations()
            assert len(gens) == 2
            names = {g["name"] for g in gens}
            assert "Gen 1" in names
            assert "Gen 2" in names
