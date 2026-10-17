"""重排器"配置无效时快速失败 + 失败闩锁"的回归测试。

背景（真实环境实测）：`.env` 里 `CLIPWRIGHT_RAG_RERANK_MODEL=qwen3-vl-rerank`
（不含 `/`，不是合法的 HF 仓库名）时，`search_shots(rerank=True)`——**每次检索的默认路径**——
会先付出 `from sentence_transformers import CrossEncoder` 的导入成本（本机约 20 秒，
另一次实测含模型加载 306 秒），**然后**才报"模型名无效"并静默退回无重排。
把"配置错了"变成"系统很慢"是最糟的一种失败形态：没人会怀疑是配置。

两条不变量：
1. 配置无效必须**毫秒级**暴露（校验在重活之前）；
2. 同样的失败**不重复付代价**（进程级闩锁），且不改变返回语义（回退原序）。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from clipwright.rag.reranker import Reranker, ScoredChunk


def _cands(n: int = 3) -> list[ScoredChunk]:
    return [ScoredChunk(id=f"c{i}", text=f"第{i}段", score=1.0 - i * 0.1, metadata={})
            for i in range(n)]


@pytest.fixture(autouse=True)
def _clear_latch():
    Reranker.reset_failure_latch()
    yield
    Reranker.reset_failure_latch()


class TestFastFail:
    def test_invalid_model_name_fails_before_import(self, monkeypatch):
        """模型名不含 `/` 时**不许**去导入 sentence_transformers。

        做法上用一个"导入即失败"的钩子来证明顺序：如果实现先 import 再校验，
        这个测试就会炸在导入上（`fake_import` 抛异常），而不是拿到 RuntimeError。
        """
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise AssertionError("校验必须在导入之前——否则每次检索白等 20 秒")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        r = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        with pytest.raises(RuntimeError, match="模型名无效"):
            r._lazy_load_local()

    def test_rerank_falls_back_fast_and_returns_original_order(self, monkeypatch):
        monkeypatch.setattr("clipwright.config.settings.rag_rerank_model", "qwen3-vl-rerank")
        r = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        cands = _cands()
        started = time.perf_counter()
        out = asyncio.run(r.rerank("q", cands, top_k=2))
        elapsed = time.perf_counter() - started
        assert [c.id for c in out] == ["c0", "c1"]      # 回退原序，不改变语义
        assert elapsed < 2.0, f"配置错误却花了 {elapsed:.1f}s（应毫秒级）"

    def test_failure_is_latched(self, monkeypatch):
        """第二次调用不许再付一次失败成本。"""
        r = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert r.unavailable_reason
        # 第二次：把"可用路径"整个换掉，若仍走它就说明闩锁没生效
        called = {"n": 0}
        monkeypatch.setattr(Reranker, "_rerank_local",
                            lambda self, *a, **kw: called.__setitem__("n", called["n"] + 1))
        out = asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert called["n"] == 0
        assert [c.id for c in out] == ["c0", "c1"]

    def test_latch_is_keyed_by_config(self, monkeypatch):
        """**闩锁必须按配置键控**，否则"改好配置"也被锁死、测试之间还会互相污染。

        实测踩到：第一版用进程级全局布尔，一个用例把闩锁打开后，后面的既有用例
        （provider 绑定、API 模式）全被短路，一次跑出 3 条失败。
        """
        broken = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        asyncio.run(broken.rerank("q", _cands(), top_k=2))
        assert broken.unavailable_reason

        # 换了配置（模型名合法）→ 应该重新尝试，不被上面的失败记录拦住
        called = {"n": 0}

        def fake_local(self, query, candidates, top_k):
            called["n"] += 1
            return candidates[:top_k]

        monkeypatch.setattr(Reranker, "_rerank_local", fake_local)
        fixed = Reranker(model_name="BAAI/bge-reranker-v2-m3", base_url="", api_key="")
        asyncio.run(fixed.rerank("q", _cands(), top_k=1))
        assert called["n"] == 1, "换了配置应该重新尝试（闩锁不该是全局布尔）"
        assert fixed.unavailable_reason is None

    def test_reset_latch_allows_retry(self, monkeypatch):
        r = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        asyncio.run(r.rerank("q", _cands(), top_k=2))
        Reranker.reset_failure_latch()
        assert r.unavailable_reason is None
        called = {"n": 0}
        monkeypatch.setattr(Reranker, "_rerank_local",
                            lambda self, *a, **kw: (called.__setitem__("n", called["n"] + 1),
                                                    a[1][:kw.get("top_k", 2)])[1])
        asyncio.run(r.rerank("q", _cands(), top_k=1))
        assert called["n"] == 1

class TestExplicitDisable:
    """`disabled` 是**有意关闭**，不是配置错误——日志级别必须不同，否则每次启动都告警。"""

    @pytest.mark.parametrize("name", ["disabled", "none", "off", "", "  DISABLED  "])
    def test_disabled_names_skip_quietly(self, name, caplog):
        import logging

        r = Reranker(model_name=name, base_url="", api_key="")
        with caplog.at_level(logging.INFO):
            out = asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert [c.id for c in out] == ["c0", "c1"]
        assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING], \
            "显式关闭不该打 WARNING"
        assert any("显式关闭" in rec.message for rec in caplog.records)

    def test_wrong_model_name_still_warns(self, caplog):
        """而"看起来像模型名但不合法"必须告警（这是配置错误）。"""
        import logging

        r = Reranker(model_name="qwen3-vl-rerank", base_url="", api_key="")
        with caplog.at_level(logging.WARNING):
            asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert any("模型名无效" in rec.message for rec in caplog.records)

    def test_disabled_is_latched_too(self):
        r = Reranker(model_name="disabled", base_url="", api_key="")
        asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert r.unavailable_reason


    def test_disabled_does_not_disable_a_provider_binding(self, monkeypatch):
        """**绑定优先于"显式关闭"**（实测踩到）。

        `.env` 把 `RAG_RERANK_MODEL` 设成 `disabled` 之后，如果把"显式关闭"的判定放在
        provider 绑定之前，就会把用户的绑定也一起关掉——绑定的自有模型根本没机会生效。
        绑定给了模型，就听绑定的。
        """
        async def fake_rerank(*_a, **_kw):
            return [ScoredChunk(id="2", text="c", score=0.9, metadata={}),
                    ScoredChunk(id="0", text="a", score=0.5, metadata={})]

        monkeypatch.setattr("clipwright.rag.reranker._bound_reranker",
                            lambda: (object(), "bge-reranker-v2-m3"))
        monkeypatch.setattr(Reranker, "_rerank_via_provider", fake_rerank)
        # 配置里是"显式关闭"，但仍然必须走绑定
        r = Reranker(model_name="disabled", base_url="", api_key="")
        out = asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert [c.id for c in out] == ["2", "0"]
        assert r.unavailable_reason is None


class TestApiFailureLatched:
    def test_api_failure_is_latched_and_falls_back(self, monkeypatch):
        r = Reranker(model_name="m", base_url="https://api.example.com/rerank", api_key="k")

        async def boom(*_a, **_kw):
            raise RuntimeError("401 unauthorized")

        monkeypatch.setattr(Reranker, "_rerank_api", boom)
        out = asyncio.run(r.rerank("q", _cands(), top_k=2))
        assert [c.id for c in out] == ["c0", "c1"]
        assert "API 模式失败" in (r.unavailable_reason or "")

    def test_empty_candidates_short_circuit(self):
        assert asyncio.run(Reranker().rerank("q", [])) == []
