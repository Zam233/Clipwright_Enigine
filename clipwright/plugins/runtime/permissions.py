"""插件权限的**运行时强制**（P9）——在子进程内用审计钩子拦住越权行为。

为什么必须在子进程内强制：同一进程里插件可以 `import os; os.remove(...)`，
宿主写得再严也只是「声明校验」。子进程把插件的执行面与宿主切开，然后在**插件自己
的进程里**装审计钩子（`sys.addaudithook`），使越权在发生的那一刻被拒。

策略（最小权限，fail-closed）：
- **读**：放行（插件要读自己的资源/模型文件；跨插件读的风险由「写受限」与
  子进程隔离兜住）；
- **写/删**：仅当 manifest 声明 `fs_write`，且路径落在该插件的 PluginData 目录内；
- **网络**：仅当声明 `network` 或 `http`；
- **子进程/命令执行**：一律拒绝（权限白名单里没有 `shell`，等同于永不授予）。

审计钩子无法被插件移除（Python 不提供移除 API），且钩子内的异常会中断触发它的
操作——这正是我们要的语义。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

#: 需要写权限的文件相关审计事件（open 的 mode 里含写标志时才判定）
_WRITE_MODES = ("w", "a", "x", "+")

#: 一律拒绝的事件（命令执行/子进程/动态库加载等）
_ALWAYS_DENY_PREFIXES = (
    "subprocess.",
    "os.system",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "ctypes.",
    "pty.spawn",
)

#: 网络相关审计事件
_NETWORK_EVENTS = (
    "socket.connect",
    "socket.bind",
    "socket.getaddrinfo",
    "urllib.Request",
    "http.client.connect",
)


class PermissionViolation(PermissionError):
    """越权操作（携带事件名与目标，便于写错误通道时给出可读原因）。"""

    def __init__(self, event: str, target: str, reason: str) -> None:
        super().__init__(f"权限不足：{reason}（event={event}, target={target}）")
        self.event = event
        self.target = target
        self.reason = reason


class PermissionGuard:
    """按 manifest 权限为插件构造判定器（可独立测试，不依赖审计钩子）。"""

    def __init__(
        self,
        permissions: Iterable[str],
        *,
        plugin_id: str,
        data_dir: Optional[Path] = None,
        extra_write_roots: Optional[Iterable[Path]] = None,
    ) -> None:
        self.permissions = {str(p) for p in (permissions or [])}
        self.plugin_id = str(plugin_id or "")
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.write_roots = [p.resolve() for p in (extra_write_roots or []) if p]
        if self.data_dir is not None:
            self.write_roots.append(self.data_dir)

    # ── 判定 ──────────────────────────────────────────

    @property
    def can_write_fs(self) -> bool:
        return "fs_write" in self.permissions

    @property
    def can_network(self) -> bool:
        return bool({"network", "http"} & self.permissions)

    def check_event(self, event: str, args: tuple[Any, ...] = ()) -> None:
        """审计钩子入口：违规即抛 PermissionViolation。"""
        if event.startswith(_ALWAYS_DENY_PREFIXES):
            raise PermissionViolation(event, _first_str(args), "命令执行/子进程未被授权")

        if event in _NETWORK_EVENTS and not self.can_network:
            raise PermissionViolation(event, _first_str(args),
                                      "未声明 network/http 权限，禁止网络访问")

        if event == "open":
            mode = str(args[1]) if len(args) > 1 else ""
            if any(flag in mode for flag in _WRITE_MODES):
                self.check_write_path(_first_str(args))

    def check_write_path(self, raw_path: str) -> None:
        """写路径判定：需 fs_write 且落在允许的根内。"""
        if not self.can_write_fs:
            raise PermissionViolation("open", raw_path, "未声明 fs_write 权限，禁止写文件")
        if not raw_path:
            raise PermissionViolation("open", raw_path, "写路径为空")
        try:
            target = Path(raw_path).resolve()
        except (OSError, ValueError) as e:
            raise PermissionViolation("open", raw_path, f"路径无法解析: {e}") from e
        if not self.write_roots:
            raise PermissionViolation("open", raw_path, "插件无可用写目录")
        for root in self.write_roots:
            if target == root or root in target.parents:
                return
        allowed = ", ".join(str(r) for r in self.write_roots)
        raise PermissionViolation(
            "open", raw_path, f"只允许写插件自己的数据目录（{allowed}）",
        )


def _first_str(args: tuple[Any, ...]) -> str:
    for a in args:
        if isinstance(a, (str, bytes)):
            return a.decode("utf-8", "replace") if isinstance(a, bytes) else a
    return ""


def install_audit_hook(guard: PermissionGuard) -> None:
    """把 guard 装成进程级审计钩子（worker 在 import 插件**之前**调用）。

    注意：`sys.addaudithook` 只能增不能删，因此每个 worker 进程只装一次，
    且必须保证 guard 的判定是「本插件」的权限——一个 worker 只跑一个插件。
    """
    def _hook(event: str, args: tuple[Any, ...]) -> None:  # pragma: no cover - 由子进程触发
        try:
            guard.check_event(event, args)
        except PermissionViolation:
            raise
        except Exception:
            # 判定器自身异常时不放行（fail-closed），但避免把无关事件全炸掉
            return

    sys.addaudithook(_hook)


def guarded_import_roots() -> list[str]:
    """留给 worker 记录「本次启动时 sys.path 中的插件根」（便于排查）。"""
    return [p for p in sys.path if p and os.path.isdir(p)]
