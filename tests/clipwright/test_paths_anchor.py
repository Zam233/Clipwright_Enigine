"""B14/B15 测试 — 路径锚定工具 anchor() 与 CWD 无关性。

核心验收：
  1. anchor() 将相对路径绝对化到包父目录（clipwright 的父目录），绝对路径原样返回。
  2. 修改 CWD 后 anchor() 结果不变（不依赖进程启动目录）。
  3. webhook 模块在 CWD 变更后重新导入，_WEBHOOKS_FILE 仍锚定在包父目录。
  4. allowed_media_roots() 的 persona/tts 目录不依赖 CWD。

Failing-first：修复前 clipwright/paths.py 不存在 / 各 API 使用 CWD-relative
Path(...)，以下断言应失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

import clipwright.paths as paths_module
from clipwright.paths import anchor

# anchor() 的锚定基座：clipwright 包的父目录（仓库根）
_PKG_PARENT = Path(paths_module.__file__).resolve().parent.parent


class TestAnchorUnit:
    def test_anchor_renders_absolute(self) -> None:
        p = anchor("renders")
        assert p.is_absolute()
        assert str(p).replace("\\", "/").endswith("/renders")
        assert p == _PKG_PARENT / "renders"

    def test_anchor_absolute_path_unchanged(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "x.json"
        assert anchor(target) == target

    def test_anchor_nested_relative(self) -> None:
        assert anchor("PluginData/voices/audio") == _PKG_PARENT / "PluginData" / "voices" / "audio"


class TestAnchorCwdIndependent:
    def test_anchor_ignores_cwd(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        p = anchor("webhooks.json")
        assert p == _PKG_PARENT / "webhooks.json"
        assert not str(p).startswith(str(tmp_path))
        assert p.parent.exists()

    def test_anchor_renders_ignores_cwd(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert anchor("renders") == _PKG_PARENT / "renders"


class TestAllowedMediaRootsCwdIndependent:
    def test_persona_and_tts_anchored(self, tmp_path: Path, monkeypatch) -> None:
        from clipwright.config import settings
        from clipwright.security import allowed_media_roots

        monkeypatch.setattr(settings, "persona_dir", Path("personas"))
        monkeypatch.setattr(settings, "tts_output_dir", Path("PluginData/voices/audio"))
        monkeypatch.chdir(tmp_path)

        roots = allowed_media_roots()
        persona_root = next(r for r in roots if r.name == "personas")
        tts_root = next(r for r in roots if r.name == "audio")

        assert persona_root.is_absolute()
        assert persona_root == _PKG_PARENT / "personas"
        assert not str(persona_root).startswith(str(tmp_path))

        assert tts_root.is_absolute()
        assert tts_root == _PKG_PARENT / "PluginData" / "voices" / "audio"


class TestWebhookAnchored:
    async def test_fresh_import_anchors_webhooks_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        sys.modules.pop("clipwright.api.webhook", None)
        import clipwright.api.webhook as w

        assert w._WEBHOOKS_FILE == _PKG_PARENT / "webhooks.json"
        assert not str(w._WEBHOOKS_FILE).startswith(str(tmp_path))

        # 隔离内存态：避免留下 active webhook 影响后续测试（monkeypatch 自动还原）
        monkeypatch.setattr(w, "_webhooks", [])
        monkeypatch.setattr(w, "_WEBHOOKS_FILE", tmp_path / "webhooks.json")
        req = w.RegisterWebhookRequest(
            url="http://8.8.8.8/webhook-test",
            events=["pipeline.completed"],
        )
        cfg = await w.register_webhook(req)
        assert cfg.webhook_id.startswith("wh_")
        assert cfg.url == "http://8.8.8.8/webhook-test"
        assert (tmp_path / "webhooks.json").exists()

        listed = await w.list_webhooks()
        assert any(x.webhook_id == cfg.webhook_id for x in listed)
