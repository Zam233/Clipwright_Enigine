"""子进程插件的宿主侧句柄（A1 修复）。

问题：`_load_subprocess()` 原本 `return None`，而 loader 的契约是「返回插件实例」。
于是所有把 `None` 当失败的调用点都对子进程插件误判：

- `POST /api/plugin/load/{id}` → 404（实际已加载）；
- `install_service.install_plugin` → 回滚并**删除已成功安装的插件目录**；
- `loader.get()` 恒为 None → `DELETE /api/plugin/{id}`（卸载）404；
- `load()` 不幂等 → 二次调用再起一个 worker，旧 worker 泄漏；
- `reload()` 开头 `if plugin_id not in self._plugins: return` → 配置改了不生效。

本句柄提供调用方期望的最小面（manifest / config / initialize / shutdown），
使「进程内」与「子进程」两种运行形态在 loader 调用方看来完全一致。
真正的贡献已由 worker 以声明形式返回、宿主办过门面注册，故 `contributions()`
返回空列表（不是"零贡献"，而是"声明不由宿主侧实例提供"）。
"""
from __future__ import annotations

from typing import Any


class SubprocessPluginHandle:
    """代表一个 runtime=subprocess 的已加载插件。"""

    def __init__(self, manifest: Any, host: Any, config: dict[str, Any] | None = None) -> None:
        self.manifest = manifest
        self.config: dict[str, Any] = dict(config or {})
        self._host = host

    # ── loader 调用方期望的属性/方法 ──────────────────

    @property
    def plugin_id(self) -> str:
        return str(getattr(self.manifest, "id", "") or "")

    def contributions(self) -> list[Any]:
        """宿主侧不提供声明（声明由 worker 返回并经门面注册）。"""
        return []

    def initialize(self) -> None:
        """启动时已完成（worker 已就绪、贡献已注册）——保持幂等空实现。"""
        return None

    def shutdown(self) -> None:
        """停止子进程（幂等：host.stop 可重复调用）。"""
        try:
            self._host.stop()
        except Exception:  # noqa: BLE001 — 关闭失败不应向上抛
            pass

    @property
    def host(self) -> Any:
        return self._host

    def __repr__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"<SubprocessPluginHandle {self.plugin_id} alive={getattr(self._host, 'alive', False)}>"


__all__ = ["SubprocessPluginHandle"]
