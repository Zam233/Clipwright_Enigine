"""Phase 4.4 回归：插件签名 CLI 与运行时 loader 签名方案互通。"""

from __future__ import annotations

from pathlib import Path

from clipwright.plugins.loader import verify_manifest_signature


def _write_plugin(plugin_dir, extra: str = "") -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        f"id: sig_test_plugin\nkind: capability\nname: 签名测试\nversion: 0.1.0\n{extra}", encoding="utf-8")


def test_sign_verify_roundtrip(tmp_path, monkeypatch):
    """CLI sign --write → 运行时 verify_manifest_signature 通过（HMAC 方案互通）。"""
    import sys

    from clipwright.config import settings
    from clipwright.plugins.loader import PluginLoader, verify_manifest_signature

    plugin_dir = tmp_path / "sig_test_plugin"
    _write_plugin(plugin_dir)

    key = "test-sign-key-0123456789abcdef"
    monkeypatch.setattr(settings, "plugin_signature_key", key)

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "plugin_sign.py"
    assert script.exists(), "scripts/plugin_sign.py 缺失"

    # CLI sign（写回）
    rc = _run_cli(script, ["sign", str(plugin_dir), "--key", key, "--write"], monkeypatch, sys)
    assert rc == 0, "sign 退出码非 0"

    # 运行时 loader 校验
    loader = PluginLoader(plugin_dir=tmp_path)
    manifest = loader._parse_manifest(plugin_dir.name, plugin_dir)
    assert manifest.signature, "签名未写回"
    assert verify_manifest_signature(manifest), "运行时 loader 无法验证 CLI 签名（方案不一致）"


def test_verify_fail_on_tamper(tmp_path, monkeypatch):
    """篡改 manifest 后 verify 失败。"""
    import sys

    from clipwright.config import settings

    plugin_dir = tmp_path / "sig_tamper_plugin"
    _write_plugin(plugin_dir)
    key = "another-key"
    monkeypatch.setattr(settings, "plugin_signature_key", key)
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "plugin_sign.py"

    assert _run_cli(script, ["sign", str(plugin_dir), "--key", key, "--write"], monkeypatch, sys) == 0
    # 篡改 name
    p = plugin_dir / "plugin.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("name: 签名测试", "name: 被篡改"), encoding="utf-8")
    rc = _run_cli(script, ["verify", str(plugin_dir), "--key", key], monkeypatch, sys)
    assert rc == 1, "篡改后 verify 应失败"


def _run_cli(script: Path, args: list[str], monkeypatch, sys) -> int:
    """在子进程运行 CLI（避免污染当前进程的 sys.argv/settings）。"""
    import subprocess
    r = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, cwd=str(script.parent.parent),
    )
    return r.returncode