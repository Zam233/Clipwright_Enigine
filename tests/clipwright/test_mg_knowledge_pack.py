"""MG 动效配方库（P2-1）测试：加载、选取、注入、许可合规。

合规是本文件的重点：配方库内容必须是**自行撰写的结构提炼**，
逐条登记来源与许可，且不得夹带上游代码片段。
"""

from __future__ import annotations

import pytest

from clipwright.animation.mg import knowledge as K
from clipwright.animation.mg.generator import MGGenerator

REQUIRED_FIELDS = ("id", "title", "intent", "structure", "source")
ALLOWED_LICENSES = {"Apache-2.0", "MIT"}


class TestPackIntegrity:
    def test_pack_loads(self) -> None:
        pack = K.load_pack()
        assert pack.get("version") == 1
        assert len(K.all_recipes()) >= 6, "配方库条目过少"

    def test_required_fields_present(self) -> None:
        for r in K.all_recipes():
            for field in REQUIRED_FIELDS:
                assert r.get(field), f"{r.get('id')} 缺字段 {field}"
            st = r["structure"]
            assert isinstance(st.get("beats"), list) and st["beats"], f"{r['id']} 无 beat"
            for beat in st["beats"]:
                assert isinstance(beat, dict) and beat.get("do"), f"{r['id']} beat 缺 do"

    def test_ids_unique(self) -> None:
        ids = [r["id"] for r in K.all_recipes()]
        assert len(ids) == len(set(ids))

    def test_license_registered_and_allowed(self) -> None:
        """每条必须登记来源仓库/路径/许可，且许可在白名单内、distilled=true。"""
        for r in K.all_recipes():
            src = r["source"]
            assert src.get("repo"), f"{r['id']} 缺 source.repo"
            assert src.get("path"), f"{r['id']} 缺 source.path"
            assert src.get("license") in ALLOWED_LICENSES, f"{r['id']} 许可未登记或不在白名单"
            assert src.get("distilled") is True, f"{r['id']} 未声明为结构提炼"

    def test_no_upstream_code_fragments(self) -> None:
        """不得夹带上游代码片段（HTML/CSS 关键帧/JS 函数）。"""
        blob = "\n".join(str(r) for r in K.all_recipes())
        for token in ("<div", "<script", "@keyframes", "function(", "gsap.", "=>"):
            assert token not in blob, f"配方库疑似包含代码片段: {token}"

    def test_defaults_resolve(self) -> None:
        for rid in K.load_pack().get("defaults") or []:
            assert K.get_recipe(str(rid)) is not None, f"defaults 指向不存在的配方 {rid}"


class TestSelection:
    def test_shot_type_and_keywords_drive_selection(self) -> None:
        picked = K.select_recipes("data_impact", "本季度营收同比增长三倍", limit=2)
        assert picked and picked[0]["id"] == "stat-count-punch"

    def test_title_reveal_selected(self) -> None:
        picked = K.select_recipes("title_reveal", "片头标题", limit=1)
        assert picked[0]["id"] == "hero-title-stagger"

    def test_shot_type_token_in_text_is_detected(self) -> None:
        # 描述里内嵌 shot_type（LLM 生成的 mg_dynamic 载荷常见）也能命中
        picked = K.select_recipes("", '{"shot_type": "comparison", "description": "新旧方案对比"}', limit=1)
        assert picked[0]["id"] == "compare-two-up"

    def test_fallback_to_defaults(self) -> None:
        picked = K.select_recipes("", "完全无关的一段普通描述", limit=2)
        assert [r["id"] for r in picked] == list(K.load_pack()["defaults"])

    def test_deterministic(self) -> None:
        a = [r["id"] for r in K.select_recipes("data_impact", "增长", limit=3)]
        b = [r["id"] for r in K.select_recipes("data_impact", "增长", limit=3)]
        assert a == b

    def test_limit_respected_and_zero(self) -> None:
        assert len(K.select_recipes("data_impact", "数据", limit=1)) == 1
        assert K.select_recipes("data_impact", "数据", limit=0) == []


class TestRenderSection:
    def test_section_contains_structure(self) -> None:
        section = K.render_section(K.select_recipes("title_reveal", "标题", limit=1))
        assert "## 动效配方参考" in section
        assert "hero-title-stagger" in section
        assert "禁忌" in section
        assert "缓动" in section

    def test_empty_recipes_render_empty(self) -> None:
        assert K.render_section([]) == ""

    def test_section_size_bounded(self) -> None:
        section = K.render_section(K.all_recipes())
        assert len(section) <= K._MAX_SECTION_CHARS + 32, "配方段落应有长度上限"


class TestGeneratorInjection:
    def test_injected_into_prompt(self, monkeypatch) -> None:
        gen = MGGenerator()
        calls: list[list[dict]] = []

        class _Resp:
            content = '{"elements": []}'

        class _LLM:
            async def generate(self, messages, **_kw):
                calls.append(messages)
                return _Resp()

        gen._llm = _LLM()  # type: ignore[assignment]
        import asyncio

        asyncio.run(gen._call_llm(
            "片头标题揭示 shot_type: title_reveal", "标题文字", {}, {}, {},
        ))
        assert calls, "LLM 未被调用"
        system = calls[0][0]["content"]
        assert "## 动效配方参考" in system
        assert "hero-title-stagger" in system

    def test_disabled_by_config(self) -> None:
        gen = MGGenerator()
        gen._config["prompt"]["knowledge_pack"] = False
        assert gen._knowledge_section("片头标题", {}) == ""

    def test_max_recipes_respected(self) -> None:
        gen = MGGenerator()
        gen._config["prompt"]["knowledge_pack"] = True
        gen._config["prompt"]["knowledge_max_recipes"] = 1
        section = gen._knowledge_section("数据 增长 对比 流程", {})
        assert section.count("### 配方：") == 1

    def test_missing_pack_does_not_raise(self, monkeypatch) -> None:
        gen = MGGenerator()

        def _boom(*_a, **_kw):
            raise RuntimeError("pack missing")

        monkeypatch.setattr("clipwright.animation.mg.knowledge.select_recipes", _boom)
        assert gen._knowledge_section("标题", {}) == ""

    def test_category_shot_type_wins(self) -> None:
        gen = MGGenerator()
        section = gen._knowledge_section("普通描述", {"shot_type": "mindmap"})
        assert "hub-radial-map" in section


class TestPackFileLicenseNote:
    def test_yaml_header_documents_sources(self) -> None:
        """YAML 头部必须写明来源与许可要求（供后续维护者遵循）。"""
        text = K._PACK_PATH.read_text(encoding="utf-8")
        assert "Apache-2.0" in text and "MIT" in text
        assert "THIRD_PARTY_NOTICES" in text
        assert "非原文照抄" in text or "结构与方法论的提炼" in text


@pytest.mark.parametrize("recipe_id", [r["id"] for r in K.all_recipes()])
def test_each_recipe_renders(recipe_id: str) -> None:
    recipe = K.get_recipe(recipe_id)
    assert recipe is not None
    section = K.render_section([recipe])
    assert recipe["title"] in section
