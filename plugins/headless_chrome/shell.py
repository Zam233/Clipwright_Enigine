"""chrome-headless-shell 的**实现**（能力方：插件拥有；engine 只留门面）。

2026-11-08 能力迁移（审计 B8-T8.1）：`clipwright/services/headless_browser.py` 里的
查找/安装/探测实现搬到这里。engine 侧 `headless_browser` 变成门面：按贡献取
`resolve/install/probe_version/install_state`，没有插件时按"未安装"处理（不伪造路径）。

两档策略（宿主行为，不属于插件知识，仍在门面 `apply_env`）：
没有 shell → 系统 Chrome 无头 + SwiftShader/CPU；有 shell → 走专用无头二进制。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from clipwright.config import logger

#: 下载安装目录（项目内，随仓库缓存清理）
BROWSERS_ROOT = Path(__file__).resolve().parents[2] / "_cache" / "browsers"

_install_state: dict[str, Any] = {"running": False, "ok": None, "message": "", "path": ""}


def _exe_name() -> str:
    return "chrome-headless-shell.exe" if os.name == "nt" else "chrome-headless-shell"


def _from_disk() -> str:
    """`_cache/browsers` 下自动探测（`@puppeteer/browsers` 的安装布局）。"""
    if not BROWSERS_ROOT.exists():
        return ""
    hits = sorted(BROWSERS_ROOT.glob(f"chrome-headless-shell*/**/{_exe_name()}"))
    return str(hits[0]) if hits else ""


def find_shell() -> str:
    """已安装的 chrome-headless-shell 路径（空 = 没装）。"""
    for getter in (
        lambda: str(os.environ.get("PRODUCER_HEADLESS_SHELL_PATH") or ""),
        _from_disk,
    ):
        try:
            p = getter()
        except Exception:  # noqa: BLE001
            p = ""
        if p and Path(p).exists():
            return p
    return ""


def install_state() -> dict[str, Any]:
    """当前安装状态（拷贝，避免调用方改写内部状态）。"""
    return dict(_install_state)


def _http_json(url: str, timeout: float = 30.0) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Clipwright/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _download(url: str, dest, timeout: float = 1800.0) -> int:
    """流式下载（带进度日志），返回字节数。"""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    req = urllib.request.Request(url, headers={"User-Agent": "Clipwright/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        expected = int(resp.headers.get("Content-Length") or 0)
        with dest.open("wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
                if expected and total % (10 << 20) < (1 << 20):
                    logger.info("HeadlessBrowser: 已下载 %.0f/%.0f MB",
                                total / 1048576, expected / 1048576)
    return total


def install_by_download(version: str = "") -> dict[str, Any]:
    """官方 JSON 找版本 → 直下 zip → 解压（npx 下载器挂死时的兜底）。"""
    import zipfile

    base = str(os.environ.get("CLIPWRIGHT_BROWSER_MIRROR") or "").strip().rstrip("/")
    try:
        if not version:
            info = _http_json(
                base + "/last-known-good-versions.json" if base else
                "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json"
            )
            version = str((info.get("channels", {}).get("Stable", {}) or {}).get("version") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("HeadlessBrowser: 取 Stable 版本失败: %s", exc)
    if not version:
        return {"ok": False, "message": "无法确定 chrome-headless-shell 版本（网络或镜像不可用）"}

    platform = "win64" if os.name == "nt" else ("mac-arm64" if "arm" in os.uname().machine else "linux64")
    name = f"chrome-headless-shell-{platform}.zip"
    if base:
        url = f"{base}/{version}/{platform}/{name}"
    else:
        url = ("https://storage.googleapis.com/chrome-for-testing-public/"
               f"{version}/{platform}/{name}")
    zip_path = BROWSERS_ROOT / f"{version}-{name}"
    try:
        logger.info("HeadlessBrowser: 直下 %s", url)
        size = _download(url, zip_path)
        if size <= 0:
            return {"ok": False, "message": "下载到 0 字节"}
        target = BROWSERS_ROOT / f"chrome-headless-shell-{version}"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target)
        try:
            zip_path.unlink()
        except OSError:
            pass
        shell = find_shell()
        if shell:
            return {"ok": True, "message": f"安装成功（{size / 1048576:.0f}MB）", "path": shell}
        return {"ok": False, "message": "解压完成但未找到可执行文件"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("HeadlessBrowser: 直下安装失败: %s", exc)
        return {"ok": False, "message": f"直下安装失败: {exc}"}


async def install_shell(timeout: float = 180.0) -> dict[str, Any]:
    """用 `@puppeteer/browsers` 安装 chrome-headless-shell（插件加载时也会调）。"""
    if _install_state.get("running"):
        return dict(_install_state)
    existing = find_shell()
    if existing:
        _install_state.update({"running": False, "ok": True,
                               "message": "已安装", "path": existing})
        return dict(_install_state)

    BROWSERS_ROOT.mkdir(parents=True, exist_ok=True)
    # 顺序很重要（实测 2026-10-29）：npx 的下载器在本机把 zip 下到 0 字节就挂死，
    # 要等到 900s 超时才走兜底。直下（urllib）走系统代理，几 MB/s 就下完。
    _install_state.update({"running": True, "ok": None, "message": "开始下载…", "path": ""})
    _direct = await asyncio.to_thread(install_by_download)
    if _direct.get("ok"):
        _install_state.update({"running": False, "ok": True,
                               "path": _direct.get("path", ""),
                               "message": _direct.get("message", "安装成功")})
        logger.info("HeadlessBrowser: %s", _install_state["message"])
        return dict(_install_state)
    logger.warning("HeadlessBrowser: 直下未成功（%s），改试 npx",
                   str(_direct.get("message"))[:120])
    # Windows 上 npx 是 npx.cmd，create_subprocess_exec 不会走 PATHEXT 解析
    import shutil as _shutil

    npx = (_shutil.which("npx") or _shutil.which("npx.cmd")
           or _shutil.which("npx.exe") or "")
    if not npx:
        _install_state.update({"running": False, "ok": False,
                               "message": "找不到 npx（需要 Node.js/npm）"})
        return dict(_install_state)
    cmd = [
        npx, "--yes", "@puppeteer/browsers", "install",
        "chrome-headless-shell@stable", "--path", str(BROWSERS_ROOT),
    ]
    # 国内网络直连 storage.googleapis.com 常见 0 字节挂死（实测 11 分钟 0 字节）。
    # 允许用镜像：CLIPWRIGHT_BROWSER_MIRROR=https://npmmirror.com/mirrors/chrome-for-testing
    mirror = str(os.environ.get("CLIPWRIGHT_BROWSER_MIRROR") or "").strip()
    if mirror:
        cmd += ["--base-url", mirror]
    _install_state.update({"running": True, "ok": None, "message": "开始安装…", "path": ""})
    logger.info("HeadlessBrowser: 安装 chrome-headless-shell → %s", BROWSERS_ROOT)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = (out or b"").decode("utf-8", errors="replace")
        shell = find_shell()
        ok = proc.returncode == 0 and bool(shell)
        if not ok:
            logger.warning("HeadlessBrowser: npx 安装未成功（exit=%s），改走直下",
                           proc.returncode)
            res = await asyncio.to_thread(install_by_download)
            if res.get("ok"):
                _install_state.update({"running": False, "ok": True,
                                       "path": res.get("path", ""),
                                       "message": res.get("message", "")})
                return dict(_install_state)
        _install_state.update({
            "running": False, "ok": ok, "path": shell,
            "message": ("安装成功" if ok else f"安装失败（exit={proc.returncode}）: {text[-400:]}"),
        })
        logger.info("HeadlessBrowser: %s", _install_state["message"])
    except asyncio.TimeoutError:
        logger.warning("HeadlessBrowser: npx 安装超时（>%.0fs），改走直下", timeout)
        res = await asyncio.to_thread(install_by_download)
        _install_state.update({"running": False, "ok": bool(res.get("ok")),
                               "path": res.get("path", ""),
                               "message": res.get("message", "")})
        return dict(_install_state)
    except FileNotFoundError:
        _install_state.update({"running": False, "ok": False,
                               "message": "找不到 npx（需要 Node.js/npm）"})
    except Exception as exc:  # noqa: BLE001
        _install_state.update({"running": False, "ok": False, "message": f"安装异常: {exc}"})
        logger.warning("HeadlessBrowser: 安装异常 %s", exc)
    return dict(_install_state)


def install_sync(timeout: float = 900.0) -> dict[str, Any]:
    """同步版（供插件加载等同步上下文调用）。"""
    import asyncio as _aio

    try:
        return _aio.run(install_shell(timeout))
    except RuntimeError:
        # 已在事件循环里 → 后台跑，不阻塞
        loop = _aio.get_running_loop()
        loop.create_task(install_shell(timeout))
        return dict(_install_state)


def probe_version(path: str) -> str:
    """取版本号（尽力而为；失败返回空）。"""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr or "").strip()[:60]
    except Exception:  # noqa: BLE001
        return ""
