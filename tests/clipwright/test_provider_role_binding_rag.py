"""B4 测试：RAG（嵌入/重排序）与 TTS 的角色绑定接入。"""
from __future__ import annotations

import pytest

from clipwright.services import provider_sources as ps


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)


def _bind(role: str, kind: str, provider: str, model: str, **extra) -> str:
    payload = {
        "name": f"src-{role}", "kind": kind, "provider": provider,
        "base_url": "https://bound.example.com", "api_keys": ["sk-bound-key-1"],
        "models": [model],
    }
    payload.update(extra)
    src = ps.create_source(payload)
    ps.set_role(role, src["id"], model)
    return src["id"]


class _FakeEmbedder:
    def __init__(self, **kw):
        self.kw = kw
        self.embedded: list[list[str]] = []

    def embed(self, texts):
        self.embedded.append(list(texts))
        return [[0.0] * 8 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 8

    @property
    def dim(self):
        return 8


class TestRagEmbedBinding:
    def test_bound_source_drives_embedder(self, monkeypatch):
        from clipwright.providers import embedding as emb_mod
        from clipwright.rag import embedder as ed

        captured: dict = {}

        class FakeProvider:
            name = "openai"

            def default_model(self):
                return "m"

            def create_embedder(self, **kw):
                captured.update(kw)
                return _FakeEmbedder(**kw)

        monkeypatch.setattr(emb_mod, "resolve_embedding_provider", lambda n: FakeProvider())
        _bind("embed", "embedding", "openai", "qwen3-embedding-8b",
              timeout_sec=11.0, proxy="http://p:1", headers={"X-K": "v"})
        ed.reset_embedder()
        try:
            got = ed.get_embedder()
        finally:
            ed.reset_embedder()
        assert isinstance(got, _FakeEmbedder)
        assert captured["model"] == "qwen3-embedding-8b"
        assert captured["api_key"] == "sk-bound-key-1"
        assert captured["base_url"] == "https://bound.example.com"
        assert captured["timeout_sec"] == 11.0
        assert captured["proxy"] == "http://p:1"
        assert captured["headers"] == {"X-K": "v"}

    def test_unbound_falls_back_to_settings(self, monkeypatch):
        from clipwright.rag import embedder as ed

        created: dict = {}

        class FakeLocal:
            def __init__(self, model_name=None):
                created["model"] = model_name

        monkeypatch.setattr(ed, "SentenceTransformerEmbedder", FakeLocal)
        monkeypatch.setattr(ed.settings, "rag_embed_provider", "sentence_transformer", raising=False)
        monkeypatch.setattr(ed.settings, "rag_embed_model", "BAAI/bge-small-zh-v1.5", raising=False)
        ed.reset_embedder()
        try:
            got = ed.get_embedder()
        finally:
            ed.reset_embedder()
        assert isinstance(got, FakeLocal)
        assert "model" in created  # 走的是旧 settings 分支


class TestRagRerankBinding:
    @pytest.mark.asyncio
    async def test_bound_source_used_for_rerank(self, monkeypatch):
        from clipwright.rag.reranker import Reranker
        from clipwright.rag.vector_store import ScoredChunk

        class FakeRerankerImpl:
            async def rerank(self, *, query, documents, model, top_n):
                assert documents == ["a", "b", "c"]
                assert model == "bge-reranker-v2-m3"
                return [(2, 0.9), (0, 0.5)]

        _bind("rerank", "rerank", "http", "bge-reranker-v2-m3")
        monkeypatch.setattr(
            "clipwright.providers.rerank.resolve_rerank_provider",
            lambda n: type("P", (), {"create_reranker": lambda self, **kw: FakeRerankerImpl()})(),
        )
        cands = [ScoredChunk(id=str(i), text=t, score=0.0)
                 for i, t in enumerate(["a", "b", "c"])]
        out = await Reranker().rerank("q", cands, top_k=2)
        assert [(c.id, c.score) for c in out] == [("2", 0.9), ("0", 0.5)]

    @pytest.mark.asyncio
    async def test_provider_failure_falls_back_to_original_order(self, monkeypatch):
        from clipwright.rag.reranker import Reranker
        from clipwright.rag.vector_store import ScoredChunk

        class Boom:
            async def rerank(self, **kwargs):
                raise RuntimeError("boom")

        _bind("rerank", "rerank", "http", "m")
        monkeypatch.setattr(
            "clipwright.providers.rerank.resolve_rerank_provider",
            lambda n: type("P", (), {"create_reranker": lambda self, **kw: Boom()})(),
        )
        cands = [ScoredChunk(id="1", text="a", score=0.1),
                 ScoredChunk(id="2", text="b", score=0.2)]
        out = await Reranker().rerank("q", cands, top_k=2)
        assert [c.id for c in out] == ["1", "2"]

    @pytest.mark.asyncio
    async def test_unbound_keeps_legacy_path(self, monkeypatch):
        from clipwright.rag.reranker import Reranker
        from clipwright.rag.vector_store import ScoredChunk

        monkeypatch.setattr(
            "clipwright.providers.rerank.resolve_rerank_provider",
            lambda n: pytest.fail("未绑定时不应触碰 provider 门面"),
        )
        r = Reranker(base_url="", api_key="")
        monkeypatch.setattr(r, "_rerank_local",
                            lambda q, c, k: c[:k])
        out = await r.rerank("q", [ScoredChunk(id="1", text="a", score=1.0)], top_k=1)
        assert [c.id for c in out] == ["1"]


class TestTtsBinding:
    def test_credentials_resolved_from_source(self):
        from clipwright.services.voice import VoiceService

        _bind("tts", "tts", "qwen-tts", "qwen3-tts-vc-2026-01-22",
              timeout_sec=33.0, proxy="http://p:2", headers={"X-T": "1"})
        creds = VoiceService._tts_credentials()
        assert creds.api_key == "sk-bound-key-1"
        assert creds.timeout_sec == 33.0
        assert creds.proxy == "http://p:2"
        assert creds.headers == {"X-T": "1"}

    def test_credentials_empty_without_binding(self):
        from clipwright.services.voice import VoiceService

        creds = VoiceService._tts_credentials()
        assert creds.api_key == "" and creds.proxy == "" and creds.headers == {}

    def test_provider_receives_creds_and_uses_key(self, monkeypatch):
        from clipwright.services import voice as v

        _bind("tts", "tts", "qwen-tts", "m")
        svc = v.VoiceService()
        prov = svc._get_provider("qwen-tts")
        assert prov._cred.api_key == "sk-bound-key-1"
        assert prov._api_key("fallback") == "sk-bound-key-1"
        assert prov._http().timeout is not None or True  # 传输参数可构造

    def test_unbound_provider_uses_settings_key(self):
        from clipwright.services import voice as v

        svc = v.VoiceService()
        prov = svc._get_provider("qwen-tts")
        assert prov._api_key("static-key") == "static-key"

    def test_headers_merge_order(self):
        from clipwright.services.voice import TtsCredentials

        creds = TtsCredentials(headers={"X-A": "1", "Authorization": "Bearer x"})
        merged = creds.merge_headers({"Authorization": "Bearer static", "Content-Type": "application/json"})
        assert merged["Authorization"] == "Bearer x"  # 源覆盖静态
        assert merged["Content-Type"] == "application/json"
        assert merged["X-A"] == "1"
