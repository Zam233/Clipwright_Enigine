"""上下文里必须说清"时间线资产是远程 URL"（E2E 2026-10-30）。

现场：时间线的素材是 Pexels 直链（渲染期 ffmpeg 取流），materials/ 是空的。
模型看到"materials 空 + 时间线指文件"就判定素材不存在，把 160 个回合全花在
反复下载/校验上，最后触闸失败。
"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import context_loader, task_blackboard as bb


def _task(task_id: str = "task_ctx_remote") -> SimpleNamespace:
    return SimpleNamespace(id=task_id, request=SimpleNamespace(
        topic="t", persona_id="", category_plugin_id="", extra={}))


def test_artifacts_preview_marks_remote_assets(tmp_path):
    bb.reset()
    bb.put("task_ctx_remote", "timeline", {
        "duration_sec": 10, "tracks": [{"id": "v", "kind": "video", "index": 0, "clips": [
            {"id": "c1", "kind": "video", "asset_id": "https://videos.pexels.com/a.mp4"},
            {"id": "c2", "kind": "video", "asset_id": "materials/video/b.mp4"},
        ]}],
    }, by="edit")
    text = context_loader._artifacts_preview(_task(), tmp_path)  # noqa: SLF001
    assert "远程 1 条" in text
    assert "不算素材缺失" in text
    bb.reset()


def test_artifacts_preview_silent_when_no_timeline(tmp_path):
    bb.reset()
    text = context_loader._artifacts_preview(_task("task_ctx_remote2"), tmp_path)  # noqa: SLF001
    assert "远程" not in text
    bb.reset()
