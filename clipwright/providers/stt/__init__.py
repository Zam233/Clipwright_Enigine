"""STT Provider **协议与注册表访问器**（实现已迁进 `plugins/stt_providers`）。

2026-11-08 能力迁移（审计 B7-T7.4）：engine 不再探测 whisper 是否安装、也不再
懒注册内置后端——

- 插件在 → `resolve_stt_backends()` 返回它声明的后端（按注册表顺序）；
- 插件被禁用 → 空列表，调用方按"没有可用的转写后端"处理（口径 A）。

转写实现与模型缓存在 `plugins/stt_providers/backends.py`；`services/stt.py` 的两个
私有方法只是 3 行转发（保留是为不打断既有调用面）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SttBackendEntry:
    """STT 后端条目：名字 + 探测 + 转写调用（`service` 由调用方注入）。"""

    name: str
    is_available: Callable[[], bool]
    call: Callable[..., Any]


def resolve_stt_backends(service: Any) -> list[tuple[str, Callable[..., Any]]]:
    """返回有序的 `(backend_name, callable)` 列表（只读注册表）。

    顺序由贡献 `meta["order"]` 决定（缺省 100，同值按 id 兜底）——**顺序是能力方的知识**，
    engine 不硬编码后端名；插件被禁用/没有贡献时返回空列表。
    """
    try:
        from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

        entries = [
            (item, int(item.meta.get("order", 100) or 100))
            for _, item in ContributionRegistry.list(ContributionKind.STT_PROVIDER)
        ]
        entries.sort(key=lambda pair: (pair[1], pair[0].id))
        return [(item.id, _bind(item.object, service)) for item, _ in entries]
    except Exception:  # noqa: BLE001 — 解析失败不阻断（调用方按无后端处理）
        return []


def _bind(entry: Any, service: Any) -> Callable[..., Any]:
    """把条目绑定到具体 service 并返回 callable。"""
    if isinstance(entry, SttBackendEntry):
        return lambda *a, **k: entry.call(service, *a, **k)
    # 第三方贡献：允许直接提供 transcribe(audio_file, **kwargs) 形态
    fn = getattr(entry, "transcribe", None)
    if callable(fn):
        return lambda audio_file, language, model_size, word_timestamps: fn(
            audio_file, language=language, model_size=model_size,
            word_timestamps=word_timestamps,
        )
    raise ValueError(f"STT provider 缺少可调用 transcribe: {entry!r}")
