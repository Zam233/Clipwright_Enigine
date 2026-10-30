# -*- coding: utf-8 -*-
"""网页素材化（PRD web-search-fetch M2）：搜索结果 → materials/ 落盘 + 来源许可。

现场：动画 Agent 的联网只把 3 条摘要塞进 prompt，图片/视频/正文从不落盘，
"动画自己找素材"实际不成立。这里锁三件事：SSRF 治理、下载即落盘（sha256 去重 +
旁车 meta）、许可诚实标注（未知就是未知）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from clipwright.services import web_assets as wa


class _Resp:
    def __init__(self, content: bytes = b"", text: str = "", ct: str = "image/jpeg"):
        self.content = content
        self.text = text
        self.headers = {"content-type": ct}

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, **kw):
        return self._responses.pop(0) if self._responses else _Resp(content=b"")


def _factory(responses):
    def make(**kw):
        return _Client(responses)
    return make


@pytest.fixture(autouse=True)
def _offline_governance(monkeypatch):
    """单测不联网：robots.txt 一律当"读不到"（= 允许），并清掉域名/缓存策略。"""
    monkeypatch.setattr(wa, "_fetch_robots_text", lambda target: "", raising=False)
    monkeypatch.setattr(wa, "_setting", lambda name, default: default, raising=False)
    wa.clear_robots_cache()
    yield
    wa.clear_robots_cache()


# ── SSRF / 许可 ─────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.png",
    "http://localhost/a.png",
    "http://10.0.0.5/a.png",
    "http://192.168.1.1/a.png",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "ftp://example.com/a.png",
    "",
])
def test_is_public_url_blocks_dangerous_targets(url):
    assert wa.is_public_url(url) is False


@pytest.mark.parametrize("url", [
    "https://images.pexels.com/photos/1/a.jpg",
    "http://example.com/a.png",
])
def test_is_public_url_allows_public(url):
    assert wa.is_public_url(url) is True


def test_license_known_and_unknown():
    assert wa.license_of("https://images.pexels.com/x.jpg") == "Pexels License"
    assert "unknown" in wa.license_of("https://example.com/x.jpg")


# ── 落盘 ───────────────────────────────────────────────────

def test_image_is_stored_with_sidecar(tmp_path: Path):
    payload = b"\x89PNG\r\n\x1a\n fake-image-bytes"
    res = asyncio.run(wa.fetch_and_store(
        "https://images.pexels.com/photos/1/pic.png", root=tmp_path,
        title="PCL 启动器截图",
        client_factory=_factory([_Resp(content=payload, ct="image/png")]),
    ))
    assert res.ok is True and res.kind == "images"
    path = Path(res.local_path)
    assert path.exists() and path.parent.name == "images"
    assert path.read_bytes() == payload
    meta = json.loads(Path(res.meta_path).read_text(encoding="utf-8"))
    assert meta["license"] == "Pexels License"
    assert meta["sha256"] == res.sha256
    assert meta["url"].startswith("https://images.pexels.com")
    assert meta["kind"] == "images"


def test_same_content_dedupes_on_disk(tmp_path: Path):
    payload = b"identical-bytes"
    for _ in range(2):
        res = asyncio.run(wa.fetch_and_store(
            "https://example.com/pic.jpg", root=tmp_path,
            client_factory=_factory([_Resp(content=payload, ct="image/jpeg")]),
        ))
        assert res.ok is True
    files = list((tmp_path / "materials" / "web" / "images").glob("*.jpg"))
    assert len(files) == 1  # sha256 去重


def test_private_url_rejected_without_network(tmp_path: Path):
    res = asyncio.run(wa.fetch_and_store("http://127.0.0.1/secret.png", root=tmp_path))
    assert res.ok is False and "不被允许" in res.error
    assert not (tmp_path / "materials").exists()


def test_oversized_file_rejected(tmp_path: Path):
    res = asyncio.run(wa.fetch_and_store(
        "https://example.com/big.mp4", root=tmp_path, max_bytes=10,
        client_factory=_factory([_Resp(content=b"x" * 100, ct="video/mp4")]),
    ))
    assert res.ok is False and "过大" in res.error


def test_video_goes_to_videos_dir(tmp_path: Path):
    res = asyncio.run(wa.fetch_and_store(
        "https://videos.pexels.com/video-files/1/clip.mp4", root=tmp_path,
        client_factory=_factory([_Resp(content=b"mp4-bytes", ct="video/mp4")]),
    ))
    assert res.ok is True and res.kind == "videos"
    assert Path(res.local_path).parent.name == "videos"


def test_ingest_results_limits_and_skips_bad(tmp_path: Path):
    results = [
        {"url": "https://images.pexels.com/a.jpg", "title": "A"},
        {"url": "http://127.0.0.1/blocked.jpg", "title": "blocked"},
        {"url": "https://images.pexels.com/b.jpg", "title": "B"},
        {"url": "https://images.pexels.com/c.jpg", "title": "C"},
    ]
    out = asyncio.run(wa.ingest_results(
        results, root=tmp_path, limit=3,
        client_factory=_factory([_Resp(content=b"a", ct="image/jpeg"),
                                 _Resp(content=b"b", ct="image/jpeg"),
                                 _Resp(content=b"c", ct="image/jpeg")]),
    ))
    # 只取了前 3 条；其中 1 条被 SSRF 拦下，其余 2 条成功
    assert len(out) == 3
    assert sum(1 for r in out if r.ok) == 2


# ── 治理（PRD web-search-fetch M3）：域名清单 / robots / 缓存 TTL ─────────

def test_host_allowlist_blocks_others(monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: (
        "pexels.com, wikimedia.org" if name == "web_assets_allowed_domains" else default
    ), raising=False)
    assert wa.host_allowed("https://images.pexels.com/a.jpg")[0] is True
    ok, why = wa.host_allowed("https://example.com/a.jpg")
    assert ok is False and "允许清单" in why


def test_host_denylist_wins_over_allowlist(monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: {
        "web_assets_allowed_domains": "pexels.com",
        "web_assets_denied_domains": "videos.pexels.com",
    }.get(name, default), raising=False)
    ok, why = wa.host_allowed("https://videos.pexels.com/clip.mp4")
    assert ok is False and "拒绝清单" in why
    assert wa.host_allowed("https://images.pexels.com/a.jpg")[0] is True


def test_host_denylist_blocks_whole_subtree(monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: (
        "bad.example" if name == "web_assets_denied_domains" else default
    ), raising=False)
    assert wa.host_allowed("https://cdn.bad.example/x.png")[0] is False
    assert wa.host_allowed("https://good.example/x.png")[0] is True


def test_fetch_rejected_by_domain_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: (
        "example.com" if name == "web_assets_denied_domains" else default
    ), raising=False)
    res = asyncio.run(wa.fetch_and_store("https://example.com/a.jpg", root=tmp_path))
    assert res.ok is False and "拒绝清单" in res.error
    assert not (tmp_path / "materials").exists()


def test_robots_disallow_blocks_fetch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: default, raising=False)
    monkeypatch.setattr(wa, "_fetch_robots_text",
                        lambda target: "User-agent: *\nDisallow: /photos\n", raising=False)
    wa.clear_robots_cache()
    res = asyncio.run(wa.fetch_and_store("https://example.com/photos/a.jpg", root=tmp_path))
    assert res.ok is False and "robots" in res.error
    # 同站允许路径照常可抓
    ok = asyncio.run(wa.fetch_and_store(
        "https://example.com/ok/a.jpg", root=tmp_path,
        client_factory=_factory([_Resp(content=b"img", ct="image/jpeg")]),
    ))
    assert ok.ok is True


def test_robots_allow_rule_beats_shorter_disallow(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: default, raising=False)
    monkeypatch.setattr(wa, "_fetch_robots_text",
                        lambda target: "User-agent: *\nDisallow: /\nAllow: /media\n",
                        raising=False)
    wa.clear_robots_cache()
    ok = asyncio.run(wa.fetch_and_store(
        "https://example.com/media/a.jpg", root=tmp_path,
        client_factory=_factory([_Resp(content=b"img2", ct="image/jpeg")]),
    ))
    assert ok.ok is True


def test_robots_can_be_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: (
        False if name == "web_assets_respect_robots" else default
    ), raising=False)
    monkeypatch.setattr(wa, "_fetch_robots_text",
                        lambda target: "User-agent: *\nDisallow: /\n", raising=False)
    wa.clear_robots_cache()
    ok = asyncio.run(wa.fetch_and_store(
        "https://example.com/private/a.jpg", root=tmp_path,
        client_factory=_factory([_Resp(content=b"img3", ct="image/jpeg")]),
    ))
    assert ok.ok is True


def test_cache_reuse_skips_second_download(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: default, raising=False)
    url = "https://images.pexels.com/photos/9/pic.png"
    first = asyncio.run(wa.fetch_and_store(
        url, root=tmp_path, title="缓存测试",
        client_factory=_factory([_Resp(content=b"cache-me", ct="image/png")]),
    ))
    assert first.ok is True and first.cached is False
    # 第二次：client_factory 换成会爆炸的，走缓存就不该被调用
    def _boom(**kw):
        raise AssertionError("缓存命中后不应再下载")
    second = asyncio.run(wa.fetch_and_store(url, root=tmp_path, client_factory=_boom))
    assert second.ok is True and second.cached is True
    assert second.local_path == first.local_path
    assert second.license == "Pexels License"


def test_cache_ttl_zero_disables_reuse(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wa, "_setting", lambda name, default: (
        0 if name == "web_assets_cache_ttl_hours" else default
    ), raising=False)
    url = "https://images.pexels.com/photos/10/pic.png"
    asyncio.run(wa.fetch_and_store(
        url, root=tmp_path, client_factory=_factory([_Resp(content=b"old", ct="image/png")]),
    ))
    assert wa.cached_copy(tmp_path, url) is None
