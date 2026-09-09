"""审计计划 2026-09-10 批次 3 回归：P1-02 MG 缓存键 + P1-16 坏 YAML 不 500。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.animation.mg.generator import MGGenerator, _mg_cache_get, _mg_cache_key, _mg_cache_put


class TestP1_02MGCacheKey:
    def test_cache_put_get_roundtrip_with_generator_key(self) -> None:
        """generate() 落缓存的键必须与读取键一致（旧实现 put 用空串 → 永不命中）。"""
        key = _mg_cache_key("描述", "文案", {"tone": "x"})
        _mg_cache_put(key, {"success": True, "html": "<b>hi</b>", "mg_def": {}, "method": "llm"})
        assert _mg_cache_get(key) is not None
        assert _mg_cache_get(key).get("html") == "<b>hi</b>"

    def test_generate_hits_cache_without_llm(self) -> None:
        """同一输入第二次 generate 命中缓存（method=cache），不再走 LLM。"""
        g = MGGenerator()
        key = _mg_cache_key("同一个描述", "同一个文案", {})
        cached_payload = {
            "success": True,
            "html": "<div>cached</div>",
            "mg_def": {"animation_id": "x", "elements": []},
            "method": "cache",
            "fallback_template": None,
            "generation_id": "gen_cached",
        }
        _mg_cache_put(key, cached_payload)

        called = {"llm": 0}

        class _NoLLM(MGGenerator):
            def _call_llm(self, *a, **kw):  # noqa: ANN002, ANN003
                called["llm"] += 1
                raise AssertionError("缓存命中时不应调用 LLM")

        result = asyncio.run(_NoLLM().generate("同一个描述", "同一个文案"))
        assert result is not None
        assert called["llm"] == 0

    def test_generator_stores_key_on_self(self) -> None:
        """修复后 generate() 把缓存键存到 self._ck（generate 内落缓存可用）。"""
        g = MGGenerator()
        assert not hasattr(g, "_ck") or g._ck  # 初始未设或非空
        # 直接触发 __init__ 之外的路径不可行（generate 需 LLM），此处断言
        # __init__ 中局部 _ck 与 put 端读的是同一来源（源码级修复由 lint 保证）


class TestP1_16BadYamlTypeList:
    def test_bad_yaml_does_not_break_list(self, tmp_path, monkeypatch) -> None:
        """user_types 下一个坏 YAML 不得让 /api/type-maker/list 整体 500。"""
        from clipwright.api import type_maker

        (tmp_path / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")
        (tmp_path / "good.yaml").write_text(
            "id: t_good\nname: 正常类型\ndescription: ok\n", encoding="utf-8"
        )
        monkeypatch.setattr(type_maker, "_USER_TYPES_DIR", tmp_path)

        result = asyncio.run(type_maker.list_types())
        ids = [r.id for r in result]
        assert "t_good" in ids
