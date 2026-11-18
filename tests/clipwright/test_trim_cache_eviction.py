"""trim cache 回归：并发渲染时不能删除本轮还要 concat 的片段。"""

from __future__ import annotations

from clipwright.services import render as render_mod


def test_trim_cache_eviction_does_not_delete_recent_file(tmp_path, monkeypatch):
    p1 = tmp_path / "trim_a.mp4"
    p2 = tmp_path / "trim_b.mp4"
    p1.write_bytes(b"a")
    p2.write_bytes(b"b")
    monkeypatch.setattr(render_mod, "_TRIM_CACHE_MAX", 1)
    with render_mod._trim_cache_lock:
        render_mod._trim_cache.clear()
    render_mod._trim_cache_put_locked("a", str(p1))
    render_mod._trim_cache_put_locked("b", str(p2))
    # 旧实现会在超过 50/1 时 unlink 最旧文件；本轮仍要 concat p1，必须保留
    assert p1.exists(), "trim cache 淘汰不能删除本轮仍在用的片段"
