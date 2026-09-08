"""MongoDbModel 持久化底座单测 — 不依赖真实 MongoDB。

用 fake db / fake cursor 替换 ``mongo._db``（与 test_pipeline_v2_persist_shape
的 monkeypatch 方式一致），覆盖：序列化往返、find_by_id 的 ObjectId/字符串
双路径、find_one/find_many 的 filter+sort+skip+limit 透传、count 返回 int。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from clipwright.context import mongo as mongo_ctx
from clipwright.services.mongodb_service import MongoDbModel


class _FakeCursor:
    """模拟 pymongo cursor 的 sort/skip/limit 链式调用与迭代。"""

    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = list(docs)
        self.ops: list[tuple[str, Any]] = []

    def sort(self, keys):
        self.ops.append(("sort", keys))
        rev = [(k, d) for k, d in keys]
        for key, direction in reversed(rev):
            self._docs.sort(key=lambda d: d.get(key, ""), reverse=direction < 0)
        return self

    def skip(self, n):
        self.ops.append(("skip", n))
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self.ops.append(("limit", n))
        self._docs = self._docs[:n] if n else self._docs
        return self

    def __iter__(self):
        return iter(self._docs)

    def __next__(self):
        return next(iter(self._docs))


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None):
        self.docs = list(docs or [])
        self.find_filter: dict | None = None
        self.find_one_calls: list[dict] = []
        self.count_calls: list[dict] = []

    def find(self, filter: dict | None = None):
        self.find_filter = filter or {}
        return _FakeCursor([d for d in self.docs if _matches(self.find_filter, d)])

    def find_one(self, filter: dict):
        self.find_one_calls.append(dict(filter))
        for d in self.docs:
            if _matches(filter, d):
                return d
        return None

    def count_documents(self, filter: dict | None = None):
        self.count_calls.append(dict(filter or {}))
        return sum(1 for d in self.docs if _matches(filter or {}, d))


def _matches(filter: dict, doc: dict) -> bool:
    return all(doc.get(k) == v for k, v in (filter or {}).items())


class _FakeDB:
    def __init__(self):
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


class _Doc(MongoDbModel):
    """最小化具体模型：集合名固定；字段仅由 kwargs 注入。

    注意 MongoDbModel.__init__ 只对「不存在的属性」赋值（hasattr 语义），
    因此这里不能预设默认值，否则 kwargs 会被吞掉。
    """

    collection_name = "fake_docs"


@pytest.fixture
def fake_db(monkeypatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(mongo_ctx, "_db", db, raising=False)
    monkeypatch.setattr(mongo_ctx, "_client", object(), raising=False)
    return db


class TestSerialization:
    def test_from_dict_to_dict_roundtrip(self) -> None:
        doc = _Doc.from_dict({"_id": "abc123", "name": "n1", "value": 3})
        assert doc.id == "abc123"
        assert doc.name == "n1"
        out = doc.to_dict()
        assert out["id"] == "abc123"
        assert "_id" not in out
        assert out["name"] == "n1"

    def test_to_database_keeps_underscore_id(self) -> None:
        doc = _Doc.from_dict({"_id": "abc123", "name": "n1"})
        out = doc.to_dict(to_database=True)
        assert out["_id"] == "abc123"
        assert "id" not in out

    def test_from_dict_parses_iso_datetime(self) -> None:
        doc = _Doc.from_dict(
            {"_id": "x", "created_time": "2026-01-02T03:04:05+00:00"}
        )
        assert isinstance(doc.created_time, datetime)

    def test_unknown_kwargs_are_set(self) -> None:
        doc = _Doc(name="a", extra_field="kept")
        assert doc.extra_field == "kept"

    def test_id_auto_generated(self) -> None:
        doc = _Doc()
        assert isinstance(doc.id, str) and len(doc.id) == 36


class TestQueries:
    def test_find_by_id_string_uuid_fallback(self, fake_db) -> None:
        col = fake_db["fake_docs"]
        col.docs = [{"_id": "d4c1a2b3-0000-0000-0000-000000000001", "name": "x"}]
        got = _Doc.find_by_id("d4c1a2b3-0000-0000-0000-000000000001")
        assert got is not None and got.name == "x"
        # UUID 非 24 位 hex，应回退为字符串 _id 查询
        assert col.find_one_calls[-1] == {"_id": "d4c1a2b3-0000-0000-0000-000000000001"}

    def test_find_by_id_missing_returns_none(self, fake_db) -> None:
        got = _Doc.find_by_id("nonexistent-uuid-value")
        assert got is None

    def test_find_one_applies_sort_and_limit(self, fake_db) -> None:
        col = fake_db["fake_docs"]
        col.docs = [
            {"_id": "1", "name": "a", "value": 1},
            {"_id": "2", "name": "b", "value": 2},
        ]
        got = _Doc.find_one({"value": 2}, sort=[("value", -1)])
        assert got is not None and got.name == "b"

    def test_find_one_no_match_returns_none(self, fake_db) -> None:
        assert _Doc.find_one({"value": 999}) is None

    def test_find_many_skip_limit(self, fake_db) -> None:
        col = fake_db["fake_docs"]
        col.docs = [{"_id": str(i), "name": f"n{i}", "value": i} for i in range(5)]
        got = _Doc.find_many({}, sort=[("value", 1)], skip=1, limit=2)
        assert [d.value for d in got] == [1, 2]

    def test_find_many_empty_filter_returns_all(self, fake_db) -> None:
        col = fake_db["fake_docs"]
        col.docs = [{"_id": "1", "name": "a", "value": 1}]
        got = _Doc.find_many()
        assert len(got) == 1

    def test_count_returns_int(self, fake_db) -> None:
        col = fake_db["fake_docs"]
        col.docs = [{"_id": str(i), "name": f"n{i}", "value": i} for i in range(3)]
        total = _Doc.count()
        assert isinstance(total, int)
        assert total == 3
        assert _Doc.count({"value": 1}) == 1
