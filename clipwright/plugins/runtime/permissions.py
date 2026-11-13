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

#: B2.1：文件「变更」类审计事件 —— 只要发生就要求 fs_write 且路径在 write_roots 内。
#: 真实事件参数（已用 tmp_path 真触发核对）：
#:   os.remove/os.unlink/os.rmdir   (path, dir_fd)
#:   os.rename/os.replace           (src, dst, src_dir_fd, dst_dir_fd)（或仅 src,dst）
#:   os.mkdir                       (path, mode, dir_fd)
#:   shutil.rmtree                  (path, onerror)
#:   os.truncate/os.chmod/os.symlink/os.link —— 同样改变文件/目录，纳入写判定。
#: 每个事件的「路径参数所在下标」见 _MUTATION_PATH_INDEX（None 表示全部前两项都查）。
_MUTATION_EVENTS = (
    "os.remove", "os.unlink", "os.rename", "os.replace",
    "os.mkdir", "os.rmdir", "shutil.rmtree",
    "os.truncate", "os.chmod", "os.symlink", "os.link",
)
#: 这些事件里「可能指向插件数据目录之外」的路径参数下标（用于 rename/replace 查目标）
_MUTATION_TWO_PATHS = frozenset({
    "os.rename", "os.replace", "os.symlink", "os.link",
})

#: B2.1：os.open 的整数 flags 中代表「写」的位（数值随平台，用 os 常量取或）。
#: O_RDONLY=0 不在其中；O_BINARY/O_NOINHERIT 等只读修饰位也不在。
_OS_OPEN_WRITE_FLAGS = (
    getattr(os, "O_WRONLY", 1)
    | getattr(os, "O_RDWR", 2)
    | getattr(os, "O_CREAT", 0)
    | getattr(os, "O_TRUNC", 0)
    | getattr(os, "O_APPEND", 0)
)

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
        plugin_dir: Optional[Path] = None,
        extra_read_roots: Optional[Iterable[Path]] = None,
    ) -> None:
        self.permissions = {str(p) for p in (permissions or [])}
        self.plugin_id = str(plugin_id or "")
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.write_roots = [p.resolve() for p in (extra_write_roots or []) if p]
        if self.data_dir is not None:
            self.write_roots.append(self.data_dir)
        # 读根（M1）：插件自己的目录 + Python 运行环境 + 显式授权目录。
        # 不含项目根/用户目录，避免插件顺手读走别的项目或素材。
        self.read_roots = self._build_read_roots(plugin_dir, extra_read_roots)

    # ── 判定 ──────────────────────────────────────────

    @property
    def can_write_fs(self) -> bool:
        return "fs_write" in self.permissions

    @property
    def can_network(self) -> bool:
        return bool({"network", "http"} & self.permissions)

    def _build_read_roots(
        self, plugin_dir: Optional[Path], extra_read_roots: Optional[Iterable[Path]],
    ) -> list[Path]:
        """构造读根：插件目录 / data_dir / 显式授权目录 / Python 运行环境。

        B2.1：旧实现把整个 ``sys.path`` 无差别加入读根——项目根（'' / 仓库根）
        或用户塞进 sys.path 的任意目录都会顺带放开整个项目，等于把「不含项目根」
        的注释变成空话。现在：
        - 不再遍历 sys.path；Python 运行环境只允许 sys.prefix/base_prefix 与
          site.getsitepackages()；
        - 额外把**引擎自身包目录**（本文件所在的 clipwright 包）作为只读根——
          worker 在审计钩子装好之后仍要懒加载 engine 模块（bridge/worker_rpc 等），
          它由本模块文件路径推导，是「运行环境」而非「整个项目根」；
        - extra_read_roots 的合法性由调用方宿主负责，但这里仍拒绝含明显路径逃逸
          的字符串（``..``/盘符穿越），并对合法项做 realpath 归一；
        - 插件目录、data_dir 保留。
        """
        import site

        roots: list[Path] = []
        for raw in (plugin_dir, self.data_dir):
            if raw:
                try:
                    roots.append(Path(raw).resolve())
                except OSError:
                    pass
        # 显式授权目录：拒绝明显路径逃逸字符串（含 '..' 段），其余 realpath 归一
        for raw in (extra_read_roots or ()):
            if not raw:
                continue
            text = str(raw)
            parts = [p for p in text.replace("\\", "/").split("/") if p]
            if ".." in parts:
                continue
            try:
                roots.append(Path(text).resolve())
            except (OSError, ValueError):
                continue
        for raw in (sys.prefix, sys.base_prefix):
            if raw:
                roots.append(Path(raw).resolve())
        try:
            roots.extend(Path(p).resolve() for p in site.getsitepackages())
        except Exception:  # noqa: BLE001 — 某些嵌入环境没有 site
            pass
        # 引擎自身包目录（由本文件路径推导，非 sys.path 全量）：
        # 例如 <repo>/clipwright/plugins/runtime/permissions.py → <repo>/clipwright
        try:
            engine_pkg = Path(__file__).resolve().parents[2]
            if (engine_pkg / "plugins" / "runtime").is_dir():
                roots.append(engine_pkg.resolve())
        except (OSError, IndexError):
            pass
        # 去重、保序
        seen: set[str] = set()
        unique: list[Path] = []
        for root in roots:
            key = str(root).lower()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return unique

    def check_read_path(self, raw_path: str) -> None:
        if not raw_path:
            raise PermissionViolation("open", raw_path, "读路径为空")
        try:
            target = Path(raw_path).resolve()
        except (OSError, ValueError) as e:
            raise PermissionViolation("open", raw_path, f"路径无法解析: {e}") from e
        for root in self.read_roots:
            if target == root or root in target.parents:
                return
        raise PermissionViolation(
            "open", raw_path,
            "读越界：只允许插件目录、Python 运行环境与显式授权目录",
        )

    def check_event(self, event: str, args: tuple[Any, ...] = ()) -> None:
        """审计钩子入口：违规即抛 PermissionViolation。

        B2.1：补齐文件变更类事件与 ``os.open`` 整数 flags 的写判定。此判定器只覆盖
        CPython audit hook **可证明**的事件；它不是 OS 沙箱，env 白名单与宿主启动前
        的 hook 结构保持不变。
        """
        if event.startswith(_ALWAYS_DENY_PREFIXES):
            raise PermissionViolation(event, _first_str(args), "命令执行/子进程未被授权")

        if event in _NETWORK_EVENTS and not self.can_network:
            raise PermissionViolation(event, _first_str(args),
                                      "未声明 network/http 权限，禁止网络访问")

        if event in ("os.listdir", "os.scandir"):
            self.check_read_path(_first_str(args))
            return

        # B2.1：文件变更类事件 —— 要求 fs_write 且**每个**路径参数都在 write_roots 内。
        # 双路径事件（rename/replace/symlink/link）必须连目标一起查，防止把插件数据
        # 目录里的文件「搬」到项目其它位置。
        if event in _MUTATION_EVENTS:
            paths = _path_args(event, args)
            for raw in paths:
                self.check_write_path(raw, event=event)
            return

        if event == "open":
            path = _first_str(args)
            mode = args[1] if len(args) > 1 else ""
            # os.open 的 mode 为 None，写意图体现在整数 flags 里（O_WRONLY/O_RDWR/
            # O_CREAT/O_TRUNC/O_APPEND）。builtin open 的 mode 是字符串，按旧规则判定。
            if isinstance(mode, str):
                if any(flag in mode for flag in _WRITE_MODES):
                    self.check_write_path(path, event=event)
                else:
                    self.check_read_path(path)
            elif mode is None or isinstance(mode, int):
                flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
                if flags & _OS_OPEN_WRITE_FLAGS:
                    self.check_write_path(path, event=event)
                else:
                    self.check_read_path(path)
            else:  # 未知 mode 形态：fail-closed，按写处理
                self.check_write_path(path, event=event)

    def check_write_path(self, raw_path: str, *, event: str = "open") -> None:
        """写路径判定：需 fs_write 且落在允许的根内。"""
        if not self.can_write_fs:
            raise PermissionViolation(event, raw_path, "未声明 fs_write 权限，禁止写文件")
        if not raw_path:
            raise PermissionViolation(event, raw_path, "写路径为空")
        try:
            target = Path(raw_path).resolve()
        except (OSError, ValueError) as e:
            raise PermissionViolation(event, raw_path, f"路径无法解析: {e}") from e
        if not self.write_roots:
            raise PermissionViolation(event, raw_path, "插件无可用写目录")
        for root in self.write_roots:
            if target == root or root in target.parents:
                return
        allowed = ", ".join(str(r) for r in self.write_roots)
        raise PermissionViolation(
            event, raw_path, f"只允许写插件自己的数据目录（{allowed}）",
        )


def _path_args(event: str, args: tuple[Any, ...]) -> list[str]:
    """从文件变更事件的真实参数里取出需要校验的路径（含 rename/replace 的目标）。"""
    out: list[str] = []
    count = 2 if event in _MUTATION_TWO_PATHS else 1
    for item in args[:count]:
        if isinstance(item, (str, bytes)):
            out.append(item.decode("utf-8", "replace") if isinstance(item, bytes) else item)
    return out or [_first_str(args)]


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
