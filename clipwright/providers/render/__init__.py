"""内置 RendererBackend 登记（方案 B-3）。

两个实现都是**适配器（adapter）而非重写**：
- `LocalFFmpegBackend` → 包装 `services/render.py::RenderService`（2569 行实现不动）
- `RemoteRenderBackend` → 包装 `services/remote_render.py::RemoteRenderService`

两者 render 签名完全一致（drop-in），适配器只做三件事：
1. 把 `RendererBackend.render(timeline, output, settings_dict, on_progress, cancel_token)`
   映射到既有服务的关键字参数（settings 字典 → width/height/fps/bitrate/…）；
2. `cancel_token → cancel_id`（既有取消机制是 contextvar + cancel_id 字符串，
   审计 P1-19 的协议级落点）；
3. 声明 `capabilities()` 与 `probe()`（后端可用性探测）。

**接线范围（诚实标注）**：本批只登记 backend + 协议符合性/映射测试；
渲染入口按 `settings.renderer_backend` 选择 backend 的切换在下一批做
（方案标注 B-3 为「最需要克制的一处」，入口切换需真实渲染回归）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

#: RendererBackend.render 的 settings 字典 → 服务关键字参数的映射
_SETTINGS_TO_KWARGS = (
    "width", "height", "fps", "bitrate", "audio_bitrate",
    "audio_file_path", "bgm_file_path", "enable_progress",
    "encoder_override", "pix_fmt_override", "force_render",
    "soft_subtitle_srt", "loudnorm_enabled",
)


def _cancel_id_of(cancel_token: Any) -> Optional[str]:
    """cancel_token → 既有服务的 cancel_id 字符串（协议级取消的统一映射）。"""
    if cancel_token is None:
        return None
    if isinstance(cancel_token, str):
        return cancel_token
    for attr in ("cancel_id", "id", "token"):
        value = getattr(cancel_token, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


@dataclass(frozen=True)
class RenderBackendEntry:
    """渲染后端条目：工厂 + 能力声明（登记进门面的载荷）。"""
    name: str
    factory: Callable[[], Any]


class LocalFFmpegBackend:
    """本机 ffmpeg 后端（包装 RenderService，不重写实现）。"""

    name = "local"

    def __init__(self, service_factory: Callable[[], Any] | None = None) -> None:
        self._service_factory = service_factory  # 测试注入；缺省懒建 RenderService
        self._service: Any | None = None

    def _svc(self) -> Any:
        if self._service is None:
            if self._service_factory is not None:
                self._service = self._service_factory()
            else:
                from clipwright.services.render import RenderService
                self._service = RenderService()
        return self._service

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "encoder": "",  # 空 = 由渲染端协商（既有逻辑）
            "pix_fmt": "yuv420p",
            "supports_gpu": False,
            "supports_soft_subtitle": True,
            "cancel": "cancel_id",  # 取消语义：contextvar + cancel_id
        }

    async def probe(self) -> bool:
        import asyncio
        import shutil

        if shutil.which("ffmpeg") is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await proc.wait() == 0
        except Exception:  # noqa: BLE001
            return False

    async def render(
        self,
        timeline: Any,
        output_path: str,
        settings: dict[str, Any],
        on_progress: Callable[[float, str], None] | None = None,
        cancel_token: Any | None = None,
        **extra: Any,
    ) -> Any:
        kwargs = {k: settings[k] for k in _SETTINGS_TO_KWARGS if k in settings}
        kwargs.update(extra)
        if on_progress is not None:
            kwargs["progress_callback"] = on_progress
        cancel_id = _cancel_id_of(cancel_token)
        if cancel_id is not None:
            kwargs["cancel_id"] = cancel_id
        svc = self._svc()
        return await svc.render(timeline, output_path, **kwargs)


class RemoteRenderBackend(LocalFFmpegBackend):
    """远程渲染后端（包装 RemoteRenderService，render 为 drop-in 同签名）。"""

    name = "remote"

    def _svc(self) -> Any:
        if self._service is None:
            if self._service_factory is not None:
                self._service = self._service_factory()
            else:
                from clipwright.services.remote_render import RemoteRenderService
                self._service = RemoteRenderService()
        return self._service

    def capabilities(self) -> dict[str, Any]:
        caps = dict(super().capabilities())
        caps.update({
            "name": self.name,
            "supports_gpu": True,   # 取决于 worker 配置，此处声明能力上限
            "supports_soft_subtitle": False,  # worker 端暂不支持 mov_text
            "cancel": "limited",    # Worker 无取消端点（审计 D7 已知）
        })
        return caps

    async def probe(self) -> bool:
        """远程后端可用性：服务可构造且（有探测方法时）自述可用。"""
        try:
            svc = self._svc()
        except Exception:  # noqa: BLE001
            return False
        for attr in ("is_available", "is_configured", "available"):
            probe = getattr(svc, attr, None)
            if callable(probe):
                try:
                    return bool(probe())
                except Exception:  # noqa: BLE001
                    return False
            if isinstance(probe, bool):
                return probe
        return True  # 无自述方法 → 交由上游选择逻辑（与既有行为一致）


def ensure_render_backends_registered() -> None:
    """登记两个内置渲染后端（幂等）。"""
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.RENDERER_BACKEND, "local") is not None:
        return
    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.RENDERER_BACKEND, "local",
                     RenderBackendEntry(name="local", factory=LocalFFmpegBackend)),
        Contribution(ContributionKind.RENDERER_BACKEND, "remote",
                     RenderBackendEntry(name="remote", factory=RemoteRenderBackend)),
    ], dispatch=False)


def resolve_render_backend(name: str) -> Any | None:
    """按名解析渲染后端实例（懒注册）。未知 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_render_backends_registered()
    impl = ContributionRegistry.resolve(ContributionKind.RENDERER_BACKEND, name)
    if impl is None:
        return None
    entry = impl.object
    if isinstance(entry, RenderBackendEntry):
        return entry.factory()
    return entry  # 插件贡献：直接是 backend 实例/协议对象


def render_backend_names() -> list[str]:
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_render_backends_registered()
    return [c.id for _, c in ContributionRegistry.list(ContributionKind.RENDERER_BACKEND)]


def resolve_preferred_render_backend() -> Any:
    """按设置选择后端（`settings.renderer_backend`，缺省 local）。"""
    from clipwright.config import settings

    name = str(getattr(settings, "renderer_backend", "") or "local")
    backend = resolve_render_backend(name)
    if backend is None:
        backend = resolve_render_backend("local")
    return backend
