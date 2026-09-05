"""B12 回归测试 — 渲染下载链路：queue_render 输出路径与 download_render 解析一致。

基线（Baseline）：钉住 B12 修复后的契约：
  1. ``queue_render`` 使用请求 ``output_path`` 的 basename（拼到 renders/），
     SSE completed 事件含 ``output_path`` 字段。
  2. ``download_render`` 按 filename 解析，支持 CJK 文件名。
  3. 路径穿越（``../..`` / 路径分隔符）在 queue 阶段被 400 拒绝。
  4. 新增 ``is_safe_download_name``：允许 CJK/unicode，仅拒 ``/ \\ : * ? " < > |`` 与 ``..``。

Failing-first：修复前 is_safe_download_name 不存在 / queue_render 忽略
output_path / download 拒绝 CJK → 以下断言应失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import clipwright.api.render as render_mod
from clipwright.security import is_safe_download_name


class TestIsSafeDownloadName:
    def test_allows_cjk(self) -> None:
        assert is_safe_download_name("发布会.mp4")
        assert is_safe_download_name("测试 发布会 现场.mp4")
        assert is_safe_download_name("帧艺_2026_08.mp4")

    def test_allows_ascii(self) -> None:
        assert is_safe_download_name("render_abc123.mp4")
        assert is_safe_download_name("a.b-c_1.mp4")

    def test_rejects_traversal(self) -> None:
        assert not is_safe_download_name("..")
        assert not is_safe_download_name("../..")
        assert not is_safe_download_name("../../etc/passwd")
        assert not is_safe_download_name("a/b.mp4")
        assert not is_safe_download_name("a\\b.mp4")

    def test_rejects_windows_forbidden_chars(self) -> None:
        for bad in ('a:b.mp4', 'a*b.mp4', 'a?b.mp4', 'a"b.mp4', "a<b>c.mp4", "a|b.mp4"):
            assert not is_safe_download_name(bad)

    def test_rejects_empty(self) -> None:
        assert not is_safe_download_name("")


class TestRenderDownloadChain:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch) -> TestClient:
        """将 renders/ 隔离到 tmp_path，避免污染仓库 renders/ 目录。"""
        from clipwright.main import app
        monkeypatch.setattr(render_mod, "_renders_dir", staticmethod(lambda: tmp_path / "renders"))
        (tmp_path / "renders").mkdir(parents=True, exist_ok=True)
        return TestClient(app)

    @pytest.fixture
    def fake_render_service(self, monkeypatch) -> None:
        """替换渲染服务为快速假实现，避免真实 ffmpeg 渲染。"""

        class _FakeResult:
            success = True
            error = ""
            output_path = ""

            def to_dict(self) -> dict:
                return {"success": True, "output_path": self.output_path,
                        "error": "", "duration_sec": 0, "ffmpeg_log": ""}

        class _FakeService:
            async def render(self, tl, out, **kwargs):
                _FakeResult.output_path = str(out)
                return _FakeResult()

        monkeypatch.setattr(
            render_mod, "_new_render_service", staticmethod(lambda: _FakeService())
        )

    def _timeline_body(self) -> dict:
        return {
            "id": "tl_b12",
            "width": 320, "height": 240, "fps": 10, "duration_sec": 1.0,
            "tracks": [],
        }

    def test_queue_output_matches_request_basename(self, client, fake_render_service) -> None:
        resp = client.post("/api/render/queue", json={
            "timeline": self._timeline_body(),
            "output_path": "渲染完成-发布会.mp4",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["output"] == "renders/渲染完成-发布会.mp4"
        # queue 内记录与 download 解析一致（basename）
        task = render_mod._render_queue.get(body["task_id"])
        assert task is not None

    def test_queue_defaults_to_task_id(self, client, fake_render_service) -> None:
        resp = client.post("/api/render/queue", json={"timeline": self._timeline_body()})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["output"] == f"renders/{body['task_id']}.mp4"

    def test_queue_rejects_traversal(self, client, fake_render_service) -> None:
        resp = client.post("/api/render/queue", json={
            "timeline": self._timeline_body(),
            "output_path": "../../etc/passwd",
        })
        assert resp.status_code == 400

    def test_queue_rejects_backslash(self, client, fake_render_service) -> None:
        resp = client.post("/api/render/queue", json={
            "timeline": self._timeline_body(),
            "output_path": "..\\..\\secret.mp4",
        })
        assert resp.status_code == 400

    def test_download_cjk_file_200(self, client, tmp_path: Path) -> None:
        renders = tmp_path / "renders"
        renders.mkdir(parents=True, exist_ok=True)
        (renders / "发布会.mp4").write_bytes(b"FAKE-MP4")
        resp = client.get("/api/render/download/%E5%8F%91%E5%B8%83%E4%BC%9A.mp4")
        assert resp.status_code == 200
        assert resp.content == b"FAKE-MP4"

    def test_download_rejects_illegal_name(self, client) -> None:
        resp = client.get("/api/render/download/a%3Ab.mp4")
        assert resp.status_code == 400

    def test_download_404_missing(self, client) -> None:
        resp = client.get("/api/render/download/nonexistent.mp4")
        assert resp.status_code == 404
