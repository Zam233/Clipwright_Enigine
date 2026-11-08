"""无头浏览器选择 —— **门面**（实现已搬进 `plugins/headless_chrome/shell.py`）。

2026-11-08 能力迁移（审计 B8-T8.1）：查找/安装/探测归插件；engine 只保留：

- **两档策略的编排**（没有 shell → 系统 Chrome 无头 + CPU；有 → 专用无头二进制）；
- 进程级 `boot_id`（前端"一次启动只提示一次"的去重键）；
- 系统 Chrome 路径探测（宿主环境知识，不是插件能力）。

没有 `headless_chrome` 插件时：`find_shell()` 为空、`install_*` 返回可读的"未安装"，
**不伪造路径**（口径 A：能力缺失是一等状态）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from clipwright.config import logger

#: 插件贡献的 id（与 plugins/headless_chrome/plugin.yaml 一致）
HEADLESS_SHELL_ID = "chrome_headless_shell"

#: 进程启动时间（模块导入即固化）——与 pid 一起构成 boot_id
_PROCESS_STARTED_AT = time.time()

#: 本次后端进程的启动标识（进程启动时间 + pid）。
_BOOT_ID = f"{int(_PROCESS_STARTED_AT)}-{os.getpid()}"


def boot_id() -> str:
    """本次后端进程的唯一标识（前端一次提示门控用）。"""
    return _BOOT_ID


def _shell_backend() -> dict[str, Any] | None:
    """取插件贡献的无头浏览器后端（按能力形状识别，不认死 id）。"""
    try:
        from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

        for _, item in ContributionRegistry.list(ContributionKind.RENDERER_BACKEND):
            obj = getattr(item, "object", None)
            if isinstance(obj, dict) and callable(obj.get("resolve")):
                return obj
    except Exception:  # noqa: BLE001 — 门面不可用 = 没装
        return None
    return None


def find_shell() -> str:
    """已安装的 chrome-headless-shell 路径（空 = 没装）。"""
    env = str(os.environ.get("PRODUCER_HEADLESS_SHELL_PATH") or "")
    if env and Path(env).exists():
        return env
    backend = _shell_backend()
    if backend is None:
        return ""
    try:
        path = str(backend["resolve"]() or "")
    except Exception:  # noqa: BLE001
        return ""
    return path if path and Path(path).exists() else ""


def _install_state() -> dict[str, Any]:
    backend = _shell_backend()
    if backend is not None:
        getter = backend.get("install_state")
        if callable(getter):
            try:
                return dict(getter() or {})
            except Exception:  # noqa: BLE001
                pass
    return {"running": False, "ok": None, "message": "", "path": ""}


def _system_chrome_path() -> str:
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(cand).exists():
            return cand
    return ""


def apply_env(env: dict[str, str]) -> dict[str, str]:
    """把浏览器选择写进 subprocess env（两档都强制"无头 + 不探 GPU"）。"""
    env.setdefault("PRODUCER_BROWSER_GPU_MODE", "software")
    env.setdefault("PRODUCER_DISABLE_GPU", "1")
    env.setdefault("PRODUCER_ENABLE_BROWSER_POOL", "1")
    shell = find_shell()
    if shell:
        env["PRODUCER_HEADLESS_SHELL_PATH"] = shell
        # 让 puppeteer 直接启动 shell（它本身就是无头专用二进制）
        env["PUPPETEER_EXECUTABLE_PATH"] = shell
        env.setdefault("HYPERFRAMES_BROWSER_PATH", shell)
    return env


def status() -> dict[str, Any]:
    """给前端/健康检查用：当前用哪种浏览器渲染。"""
    shell = find_shell()
    state = _install_state()
    backend = _shell_backend()
    install_dir = ""
    if backend is not None:
        install_dir = str(backend.get("install_dir") or "")
    return {
        "boot_id": _BOOT_ID,
        "headless_shell": {
            "installed": bool(shell),
            "path": shell,
            "id": HEADLESS_SHELL_ID,
            "install_dir": install_dir,
            "installing": bool(state.get("running")),
            "last_result": {
                "ok": state.get("ok"),
                "message": state.get("message"),
            },
        },
        # 两档模式：装了 shell 用它；否则系统 Chrome 无头 + 软件渲染
        "mode": "chrome-headless-shell" if shell else "system-chrome-headless-cpu",
        "effective_renderer": "chrome-headless-shell" if shell else "system-chrome-headless-cpu",
        "fallback": {
            "chrome_path": _system_chrome_path(),
            "gpu_mode": "software",
            "active": not bool(shell),
            "note": "无 shell 时用系统 Chrome 无头 + SwiftShader（CPU）渲染",
        },
    }


def _missing() -> dict[str, Any]:
    return {
        "ok": False,
        "message": "未安装 headless_chrome 插件：动画渲染继续使用系统 Chrome 无头 + CPU（不阻断）",
    }


async def install_shell(timeout: float = 180.0) -> dict[str, Any]:
    """安装 chrome-headless-shell（实现归插件）。"""
    backend = _shell_backend()
    if backend is None or not callable(backend.get("install")):
        return _missing()
    return await backend["install"](timeout)


def install_sync(timeout: float = 900.0) -> dict[str, Any]:
    """同步版（供插件加载等同步上下文调用）。"""
    backend = _shell_backend()
    if backend is None or not callable(backend.get("install_sync")):
        return _missing()
    return backend["install_sync"](timeout)


def install_by_download(version: str = "") -> dict[str, Any]:
    """直下 zip 兜底（实现归插件）。"""
    backend = _shell_backend()
    if backend is None or not callable(backend.get("install_by_download")):
        return _missing()
    return backend["install_by_download"](version)


def probe_version(path: str) -> str:
    """取版本号（尽力而为；失败返回空）。"""
    backend = _shell_backend()
    if backend is None or not callable(backend.get("probe_version")):
        return ""
    try:
        return str(backend["probe_version"](path) or "")
    except Exception:  # noqa: BLE001
        logger.debug("probe_version 失败（插件实现异常）", exc_info=True)
        return ""
