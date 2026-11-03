"""无头浏览器两档策略（用户 2026-10-28）：

1. 没有 chrome-headless-shell → 系统 Chrome **无头 + 软件 GPU（CPU）**；
2. 装了（插件 `headless_chrome`）→ 动画渲染走 chrome-headless-shell。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.services import headless_browser as hb


def test_apply_env_forces_headless_software_gpu(monkeypatch):
    monkeypatch.setattr(hb, "find_shell", lambda: "")
    env = hb.apply_env({})

    assert env["PRODUCER_BROWSER_GPU_MODE"] == "software", "无 shell 时必须 CPU 软件渲染"
    assert env["PRODUCER_DISABLE_GPU"] == "1"
    assert env["PRODUCER_ENABLE_BROWSER_POOL"] == "1"
    assert "PRODUCER_HEADLESS_SHELL_PATH" not in env, "没装 shell 时不要写这个变量"


def test_apply_env_uses_shell_when_present(monkeypatch, tmp_path):
    fake = tmp_path / "chrome-headless-shell.exe"
    fake.write_bytes(b"stub")
    monkeypatch.setattr(hb, "find_shell", lambda: str(fake))

    env = hb.apply_env({})

    assert env["PRODUCER_HEADLESS_SHELL_PATH"] == str(fake)
    assert env["PUPPETEER_EXECUTABLE_PATH"] == str(fake), "puppeteer 直接启动无头 shell"
    assert env["PRODUCER_BROWSER_GPU_MODE"] == "software"


def test_status_reports_two_modes(monkeypatch):
    monkeypatch.setattr(hb, "find_shell", lambda: "")
    st = hb.status()
    assert st["mode"] == "system-chrome-headless-cpu"
    assert st["headless_shell"]["installed"] is False

    monkeypatch.setattr(hb, "find_shell", lambda: r"C:\x\chrome-headless-shell.exe")
    st2 = hb.status()
    assert st2["mode"] == "chrome-headless-shell"
    assert st2["headless_shell"]["installed"] is True


def test_find_shell_prefers_env(monkeypatch, tmp_path):
    fake = tmp_path / "chrome-headless-shell.exe"
    fake.write_bytes(b"stub")
    monkeypatch.setenv("PRODUCER_HEADLESS_SHELL_PATH", str(fake))

    assert hb.find_shell() == str(fake)


def test_plugin_manifest_declares_renderer_backend():
    """插件必须声明 renderer_backend 贡献，否则装了也不生效。"""
    import yaml

    root = Path(__file__).resolve().parents[1] / "plugins" / "headless_chrome"
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    contribs = manifest.get("contributions") or []
    assert any(c.get("kind") == "renderer_backend"
               and c.get("id") == hb.HEADLESS_SHELL_ID for c in contribs)
    assert (root / "main.py").exists()


# ── 前端全局提示（用户 2026-10-29）：插件声明式贡献 app.overlay ──────

def _plugin_module():
    import importlib

    return importlib.import_module("plugins.headless_chrome.main")


def test_manifest_declares_app_overlay_slot():
    """刷新一次页面就弹框的能力，必须由 manifest 声明授权。"""
    import yaml

    root = Path(__file__).resolve().parents[1] / "plugins" / "headless_chrome"
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    contribs = manifest.get("contributions") or []
    overlay = [c for c in contribs if c.get("kind") == "ui_slot"]
    assert overlay, "缺少 ui_slot 声明"
    assert overlay[0]["id"] == "headless_chrome_install_prompt"
    assert (overlay[0].get("meta") or {}).get("slot") == "app.overlay"


def test_overlay_contribution_is_declarative_payload():
    """浮层内容必须是纯声明（端点 + 文案），前端不认识 chrome 也能渲染。"""
    plugin = _plugin_module().HeadlessChromePlugin()
    ui = [c for c in plugin.contributions() if c.kind.value == "ui_slot"]
    assert len(ui) == 1
    meta = ui[0].meta

    assert meta["slot"] == "app.overlay"
    payload = meta["payload"]
    assert payload["kind"] == "installable-dependency"
    assert payload["probe"]["endpoint"] == "/api/render/browser"
    assert payload["probe"]["path"] == "headless_shell"
    assert payload["probe"]["installedField"] == "installed"
    assert payload["install"]["endpoint"] == "/api/render/browser/install"
    assert payload["install"]["method"] == "POST"
    # 文案自带（前端不硬编码任何 chrome 专属措辞）
    copy = payload["copy"]
    assert copy["title"] and copy["why"] and copy["installLabel"] and copy["successTitle"]
    # 声明式：不允许出现可执行片段
    dumped = str(payload)
    assert "function" not in dumped and "<script" not in dumped


def test_setup_installs_when_shell_missing(monkeypatch):
    """副作用必须挂在 setup()：initialize() 在 contributions() 非空时会被门面短路。"""
    called: list[str] = []
    monkeypatch.setattr(hb, "find_shell", lambda: "")
    monkeypatch.setattr(hb, "install_sync", lambda *a, **k: called.append("install_sync") or {"ok": True})

    plugin = _plugin_module().HeadlessChromePlugin()
    assert hasattr(plugin, "setup"), "缺 setup() → 自动安装永远不会被触发"
    plugin.setup()

    assert called == ["install_sync"]


def test_setup_is_noop_when_shell_present(monkeypatch, tmp_path):
    fake = tmp_path / "chrome-headless-shell.exe"
    fake.write_bytes(b"stub")
    called: list[str] = []
    monkeypatch.setattr(hb, "find_shell", lambda: str(fake))
    monkeypatch.setattr(hb, "install_sync", lambda *a, **k: called.append("install_sync"))

    _plugin_module().HeadlessChromePlugin().setup()

    assert called == []


def test_initialize_body_has_no_install_side_effect(monkeypatch):
    """安装只有一个实现点：setup()。

    门面桥接（base._install_dual_track_bridge）会在 initialize() 前先跑 setup()，
    随后因 contributions() 非空而短路。若 initialize 里再写一份安装，就会重复下载
    120MB——所以这里用源码断言哨兵，避免真跑 initialize（那会顺带把贡献注册进
    全局 registry，污染其它插件测试）。
    """
    import inspect

    src = inspect.getsource(_plugin_module().HeadlessChromePlugin.initialize)
    assert "install_sync" not in src and "install_shell" not in src
    assert "create_task" not in src

    setup_src = inspect.getsource(_plugin_module().HeadlessChromePlugin.setup)
    assert "install_shell" in setup_src or "install_sync" in setup_src


# ── 用户 2026-10-29：不阻断 + 一次启动只提示一次 ─────────────────

def test_status_exposes_boot_id_and_effective_renderer(monkeypatch):
    """boot_id 供前端做「一次后端启动只提示一次」；effective_renderer 说明当前真在用什么。"""
    monkeypatch.setattr(hb, "find_shell", lambda: "")
    st = hb.status()
    assert st["boot_id"] and isinstance(st["boot_id"], str)
    assert st["effective_renderer"] == "system-chrome-headless-cpu"
    assert st["fallback"]["active"] is True, "没有 shell 时应明确处于 CPU 无头回退（不阻断）"

    monkeypatch.setattr(hb, "find_shell", lambda: r"C:\x\chrome-headless-shell.exe")
    st2 = hb.status()
    assert st2["effective_renderer"] == "chrome-headless-shell"
    assert st2["fallback"]["active"] is False


def test_boot_id_stable_within_process():
    assert hb.boot_id() == hb.boot_id() == hb.status()["boot_id"]


def test_overlay_payload_declares_once_backend_boot():
    """「启动一次后端只弹窗一次」由插件声明 + 宿主实现，不写死在前端。"""
    plugin = _plugin_module().HeadlessChromePlugin()
    ui = [c for c in plugin.contributions() if c.kind.value == "ui_slot"][0]
    payload = ui.meta["payload"]

    once = payload["once"]
    assert once["scope"] == "backend-boot"
    assert once["bootIdField"] == "boot_id"
    # 文案必须说明「没装也不影响，已在用 CPU 无头渲染」——弹框不是拦截
    assert "CPU" in payload["copy"]["why"] or "cpu" in payload["copy"]["why"].lower()
    assert "可选" in payload["copy"]["why"] or "不影响" in payload["copy"]["why"]
