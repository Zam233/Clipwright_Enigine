"""火山引擎生成插件单测：Seedream 文生图 / Seedance 文生视频 / 豆包音乐（V4 签名）。

全部请求经 FakeClient 回放脚本响应，不触网；产物落地通过 monkeypatch.chdir 隔离到
tmp_path；下载 URL 的 DNS 校验（assert_public_url）按用例选择性 no-op。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from functools import reduce
from pathlib import Path

import pytest

PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

import ai_image_gen.main as img_mod  # noqa: E402
import ai_music_gen.main as mus_mod  # noqa: E402
import ai_video_gen.main as vid_mod  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"fake-video"
MP3_BYTES = b"ID3" + b"fake-audio"


class FakeResp:
    def __init__(self, jdata=None, content=b"", status=200):
        self._j = jdata if jdata is not None else {}
        self.status_code = status
        self.content = content

    def json(self):
        return self._j

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """按脚本顺序回放响应：entries 每项 (method, url 子串, FakeResp)，消费首个匹配项。"""

    entries: list = []
    calls: list = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, content=None, json=None, **kw):
        FakeClient.calls.append(("POST", url, headers, content if content is not None else json))
        return self._dispatch("POST", url)

    async def get(self, url, headers=None, **kw):
        FakeClient.calls.append(("GET", url, headers, None))
        return self._dispatch("GET", url)

    def _dispatch(self, method, url):
        for i, (m, pat, resp) in enumerate(type(self).entries):
            if m == method and pat in url:
                type(self).entries.pop(i)
                return resp
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def fake_http(monkeypatch, tmp_path):
    """接管三个插件模块的 httpx.AsyncClient 与 DNS 校验，工作目录切到 tmp_path。"""
    FakeClient.entries = []
    FakeClient.calls = []
    for mod in (img_mod, vid_mod, mus_mod):
        monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(mod, "assert_public_url", lambda url: None)
    monkeypatch.chdir(tmp_path)
    return FakeClient


# ── 图片：Seedream ───────────────────────────────────────


def _as_dict(body_raw):
    """请求体可能是 dict（httpx json=）或 bytes（content=），统一为 dict。"""
    if isinstance(body_raw, dict):
        return body_raw
    return json.loads(body_raw)


def test_snap_size_converges_to_valid_pixel_range():
    assert img_mod._snap_size(1024, 576) == "1280x720"  # 下限收敛且保持 16:9
    assert img_mod._snap_size(2048, 2048) == "2048x2048"  # 区间内不动
    w, h = img_mod._snap_size(4096, 4096).split("x")
    assert 921_600 <= int(w) * int(h) <= 4_624_220  # 上限收敛


def test_image_volcengine_url_branch_downloads_local(fake_http, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    tool = img_mod.AIImageGenTool(provider="volcengine", api_key="ark-key")
    fake_http.entries = [
        ("POST", "/images/generations",
         FakeResp({"data": [{"url": "https://cdn.example.com/gen/x.png", "size": "1280x720"}]})),
        ("GET", "https://cdn.example.com/gen/x.png", FakeResp(content=PNG_BYTES)),
    ]
    out = asyncio.run(tool.execute(prompt="赛博朋克城市夜景", width=1024, height=576))

    assert out["success"] is True
    assert out["provider"] == "volcengine"
    assert Path(out["path"]).read_bytes() == PNG_BYTES  # 产物已落地
    assert out["remote_url"].endswith("x.png")

    method, url, headers, body_raw = fake_http.calls[0]
    assert (method, url) == ("POST", "/images/generations")
    assert headers["Authorization"] == "Bearer ark-key"
    body = _as_dict(body_raw)
    assert body["model"] == img_mod.DEFAULT_SEEDREAM_MODEL
    assert body["watermark"] is False
    assert body["response_format"] == "url"
    assert body["size"] == "1280x720"  # 1024x576 被收敛
    assert body["prompt"] == "赛博朋克城市夜景"


def test_image_volcengine_b64_branch_and_size_passthrough(fake_http):
    tool = img_mod.AIImageGenTool(provider="volcengine", api_key="k")
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake_http.entries = [
        ("POST", "/images/generations",
         FakeResp({"data": [{"b64_json": b64, "url": ""}]})),
    ]
    out = asyncio.run(tool.execute(prompt="封面图", size="2K"))
    assert out["success"] is True
    assert Path(out["path"]).read_bytes() == PNG_BYTES
    body = _as_dict(fake_http.calls[0][3])
    assert body["size"] == "2K"  # 显式 size 直传，不经收敛
    # b64 分支不产生下载 GET
    assert all(c[0] == "POST" for c in fake_http.calls)


def test_image_missing_key_fails_fast(fake_http, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    tool = img_mod.AIImageGenTool(provider="volcengine", api_key="")
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "ARK_API_KEY" in out["error"]


def test_image_download_rejects_private_url(monkeypatch):
    """下载 URL 指向回环地址时必须被 SSRF 校验拦截（还原真实 assert_public_url）。"""
    from clipwright.security import assert_public_url as real_assert
    monkeypatch.setattr(img_mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(img_mod, "assert_public_url", real_assert)
    FakeClient.entries = [
        ("POST", "/images/generations",
         FakeResp({"data": [{"url": "http://127.0.0.1:9/x.png"}]})),
    ]
    tool = img_mod.AIImageGenTool(provider="volcengine", api_key="k")
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "禁止访问" in out["error"]


def test_image_default_provider_via_plugin_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(img_mod.ToolRegistry, "register",
                        classmethod(lambda cls, tool, plugin_id="": captured.update(tool=tool)))
    plugin = img_mod.AIImageGenPlugin()
    plugin.config = None
    plugin.initialize()
    assert captured["tool"]._provider == "volcengine"


# ── 视频：Seedance（异步任务） ───────────────────────────


def _vid_tool():
    return vid_mod.AIVideoGenTool(provider="volcengine", api_key="ark-key",
                                  poll_interval=0.01, poll_timeout=5.0)


def test_video_volcengine_success_poll_and_download(fake_http):
    tool = _vid_tool()
    fake_http.entries = [
        ("POST", "/contents/generations/tasks", FakeResp({"id": "cgt-abc"})),
        ("GET", "/contents/generations/tasks/cgt-abc", FakeResp({"status": "queued"})),
        ("GET", "/contents/generations/tasks/cgt-abc", FakeResp({"status": "running"})),
        ("GET", "/contents/generations/tasks/cgt-abc",
         FakeResp({"status": "succeeded", "content": {"video_url": "https://cdn.example.com/v/ok.mp4"}})),
        ("GET", "https://cdn.example.com/v/ok.mp4", FakeResp(content=MP4_BYTES)),
    ]
    out = asyncio.run(tool.execute(prompt="无人机航拍海岸线", duration_sec=5))

    assert out["success"] is True
    assert out["task_id"] == "cgt-abc"
    assert Path(out["path"]).read_bytes() == MP4_BYTES

    method, url, headers, body_raw = fake_http.calls[0]
    assert (method, url) == ("POST", "/contents/generations/tasks")
    body = json.loads(body_raw)
    assert body["model"] == vid_mod.DEFAULT_SEEDANCE_MODEL
    assert body["content"] == [{"type": "text", "text": "无人机航拍海岸线"}]
    assert body["resolution"] == "720p"
    assert body["ratio"] == "16:9"
    assert body["duration"] == 5
    assert body["watermark"] is False
    # 轮询走 GET 任务路径
    assert any(c[0] == "GET" and "/contents/generations/tasks/cgt-abc" in c[1] for c in fake_http.calls)


def test_video_volcengine_i2v_first_frame(fake_http):
    tool = _vid_tool()
    fake_http.entries = [
        ("POST", "/contents/generations/tasks", FakeResp({"id": "cgt-i2v"})),
        ("GET", "/contents/generations/tasks/cgt-i2v",
         FakeResp({"status": "succeeded", "content": {"video_url": "https://cdn.example.com/v/i2v.mp4"}})),
        ("GET", "https://cdn.example.com/v/i2v.mp4", FakeResp(content=MP4_BYTES)),
    ]
    out = asyncio.run(tool.execute(prompt="让画面动起来", image_url="https://img.example.com/first.png"))
    assert out["success"] is True
    body = json.loads(fake_http.calls[0][3])
    assert body["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://img.example.com/first.png"},
        "role": "first_frame",
    }


def test_video_volcengine_failed_status_surfaces_error(fake_http):
    tool = _vid_tool()
    fake_http.entries = [
        ("POST", "/contents/generations/tasks", FakeResp({"id": "cgt-bad"})),
        ("GET", "/contents/generations/tasks/cgt-bad",
         FakeResp({"status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected",
                                                 "message": "疑似敏感"}})),
    ]
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "OutputVideoSensitiveContentDetected" in out["error"]
    assert out["task_id"] == "cgt-bad"


def test_video_volcengine_timeout(fake_http):
    tool = vid_mod.AIVideoGenTool(provider="volcengine", api_key="k",
                                  poll_interval=0.01, poll_timeout=0.05)
    fake_http.entries = [
        ("POST", "/contents/generations/tasks", FakeResp({"id": "cgt-slow"})),
        ("GET", "/contents/generations/tasks/cgt-slow", FakeResp({"status": "running"})),
        ("GET", "/contents/generations/tasks/cgt-slow", FakeResp({"status": "running"})),
        ("GET", "/contents/generations/tasks/cgt-slow", FakeResp({"status": "running"})),
        ("GET", "/contents/generations/tasks/cgt-slow", FakeResp({"status": "running"})),
        ("GET", "/contents/generations/tasks/cgt-slow", FakeResp({"status": "running"})),
    ]
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert out["error"] == "生成超时"


def test_video_missing_key_fails_fast(fake_http, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    tool = vid_mod.AIVideoGenTool(provider="volcengine", api_key="")
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "ARK_API_KEY" in out["error"]


# ── 音乐：豆包音乐大模型（V4 签名） ──────────────────────


def _ref_signature(ak, sk, action, body, ts):
    """独立重写的参考签名算法（与官方 SDK 逻辑一致），用于交叉验证。"""
    host = "open.volcengineapi.com"
    bh = hashlib.sha256(body.encode()).hexdigest()
    headers = {"content-type": "application/json; charset=utf-8", "host": host,
               "x-content-sha256": bh, "x-date": ts}
    signed_keys = sorted(headers.keys())
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in signed_keys)
    signed_headers = ";".join(signed_keys)
    canonical_request = (f"POST\n/\nAction={action}&Version=2024-08-12\n"
                         f"{canonical_headers}\n{signed_headers}\n{bh}")
    scope = f"{ts[:8]}/cn-beijing/imagination/request"
    sts = f"HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    key = reduce(lambda k, v: hmac.new(k, v.encode(), hashlib.sha256).digest(),
                 [ts[:8], "cn-beijing", "imagination", "request"], sk.encode())
    return hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()


@pytest.mark.parametrize("action,body", [
    ("GenBGM", json.dumps({"Text": "轻快的背景音乐", "Duration": 60, "Version": "v5.0"}, ensure_ascii=False)),
    ("GenSongV4", json.dumps({"Prompt": "关于星空的歌"}, ensure_ascii=False)),
    ("QuerySong", json.dumps({"TaskID": "t-1"}, ensure_ascii=False)),
])
def test_music_signature_matches_reference(action, body):
    ts = "20260901T000000Z"
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    headers = mus_mod._sign_v4("AKTEST", "SKTEST", action, body.encode("utf-8"), now)
    assert headers["X-Date"] == ts
    assert headers["Authorization"].startswith(
        "HMAC-SHA256 Credential=AKTEST/20260901/cn-beijing/imagination/request, ")
    expected = _ref_signature("AKTEST", "SKTEST", action, body, ts)
    assert headers["Authorization"].endswith("Signature=" + expected)


def test_music_bgm_success_flow(fake_http, monkeypatch):
    monkeypatch.delenv("VOLC_ACCESS_KEY", raising=False)
    monkeypatch.delenv("VOLC_SECRET_KEY", raising=False)
    tool = mus_mod.AIMusicGenTool(access_key="ak", secret_key="sk",
                                  poll_interval=0.01, poll_timeout=5.0)
    fake_http.entries = [
        ("POST", "Action=GenBGM", FakeResp({"Code": 0, "Message": "success",
                                            "Result": {"TaskID": "t-bgm", "PredictedWaitTime": 60}})),
        ("POST", "Action=QuerySong", FakeResp({"Result": {"TaskID": "t-bgm", "Status": 1, "Progress": 30}})),
        ("POST", "Action=QuerySong",
         FakeResp({"Result": {"TaskID": "t-bgm", "Status": 2, "Progress": 100,
                              "SongDetail": {"AudioUrl": "https://cdn.example.com/a/song.mp3",
                                             "Duration": 60.1}}})),
        ("GET", "https://cdn.example.com/a/song.mp3", FakeResp(content=MP3_BYTES)),
    ]
    out = asyncio.run(tool.execute(prompt="轻快的企业宣传背景音乐", duration_sec=10))

    assert out["success"] is True
    assert out["task_id"] == "t-bgm"
    assert out["instrumental"] is True
    assert Path(out["path"]).read_bytes() == MP3_BYTES

    create_body = json.loads(fake_http.calls[0][3])
    assert create_body["Text"] == "轻快的企业宣传背景音乐"
    assert create_body["Duration"] == 30  # 10s 被钳制到 v5.0 下限 30
    assert create_body["Version"] == "v5.0"
    # QuerySong 轮询体
    poll_body = json.loads(fake_http.calls[1][3])
    assert poll_body == {"TaskID": "t-bgm"}
    # 创建与轮询均带 V4 签名头
    assert fake_http.calls[0][2]["Authorization"].startswith("HMAC-SHA256 Credential=ak/")


def test_music_postpaid_uses_for_time_action(fake_http):
    tool = mus_mod.AIMusicGenTool(access_key="ak", secret_key="sk", billing_mode="postpaid",
                                  poll_interval=0.01, poll_timeout=5.0)
    fake_http.entries = [
        ("POST", "Action=GenBGMForTime", FakeResp({"Code": 0, "Result": {"TaskID": "t-p"}})),
        ("POST", "Action=QuerySong",
         FakeResp({"Result": {"TaskID": "t-p", "Status": 2,
                              "SongDetail": {"AudioUrl": "https://cdn.example.com/a/x.mp3"}}})),
        ("GET", "https://cdn.example.com/a/x.mp3", FakeResp(content=MP3_BYTES)),
    ]
    out = asyncio.run(tool.execute(prompt="咖啡馆爵士"))
    assert out["success"] is True
    assert "Action=GenBGMForTime" in fake_http.calls[0][1]


def test_music_song_mode_prompt_lyrics_and_failure(fake_http):
    tool = mus_mod.AIMusicGenTool(access_key="ak", secret_key="sk",
                                  poll_interval=0.01, poll_timeout=5.0)
    fake_http.entries = [
        ("POST", "Action=GenSongV4", FakeResp({"Code": 0, "Result": {"TaskID": "t-song"}})),
        ("POST", "Action=QuerySong",
         FakeResp({"Result": {"TaskID": "t-song", "Status": 3,
                              "FailureReason": {"Code": 300061, "Msg": "InputLyricsPlagiarized"}}})),
    ]
    out = asyncio.run(tool.execute(prompt=" ignored", instrumental=False,
                                   lyrics="[verse] 天青色等烟雨", genre="Pop",
                                   model_version="v4.3", duration_sec=999))
    assert out["success"] is False
    assert "300061" in out["error"] and "InputLyricsPlagiarized" in out["error"]

    body = json.loads(fake_http.calls[0][3])
    assert body["Lyrics"] == "[verse] 天青色等烟雨"  # Lyrics 优先于 Prompt
    assert "Prompt" not in body
    assert body["Genre"] == "Pop"
    assert body["ModelVersion"] == "v4.3"
    assert body["Duration"] == 240  # 999 被钳制到上限 240
    assert body["VodFormat"] == "mp3"


def test_music_create_api_error_surfaces(fake_http):
    tool = mus_mod.AIMusicGenTool(access_key="ak", secret_key="sk",
                                  poll_interval=0.01, poll_timeout=5.0)
    fake_http.entries = [
        ("POST", "Action=GenBGM",
         FakeResp({"ResponseMetadata": {"Error": {"Code": "QuotaExceeded", "Message": "额度不足"}}})),
    ]
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "QuotaExceeded" in out["error"]


def test_music_missing_keys_fails_fast(fake_http, monkeypatch):
    for env in ("VOLC_ACCESS_KEY", "VOLCENGINE_ACCESS_KEY", "VOLC_SECRET_KEY", "VOLCENGINE_SECRET_KEY"):
        monkeypatch.delenv(env, raising=False)
    tool = mus_mod.AIMusicGenTool(access_key="", secret_key="")
    out = asyncio.run(tool.execute(prompt="任意"))
    assert out["success"] is False
    assert "VOLC_ACCESS_KEY" in out["error"]
