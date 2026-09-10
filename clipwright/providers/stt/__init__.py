"""内置 STT Provider 注册（方案 B-2 后半）。

两个 whisper 后端实现留在 `services/stt.py`（实现体不动）；本模块把
「后端名 + 探测 + 转写调用」登记进门面：

- 目标一：转写后端**顺序**由注册表决定（whisper → faster-whisper → 插件
  新增的 STT provider），`_transcribe_any` 不再硬编码两段 try；
- 目标二：`/api/plugin/contributions?kind=stt_provider` 可列出 STT 后端，
  第三方可贡献自己的 STT provider（门面即注册表）。

行为兼容：注册表不可用时 `_transcribe_any` 回退旧的硬编码顺序。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SttBackendEntry:
    """STT 后端条目：名字 + 探测 + 转写调用（service 归属由调用方注入）。"""
    name: str
    is_available: Callable[[], bool]
    call: Callable[..., Any]


def _probe_whisper() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("whisper") is not None
    except Exception:  # noqa: BLE001
        return False


def _probe_faster_whisper() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:  # noqa: BLE001
        return False


def ensure_stt_providers_registered() -> None:
    """登记两个内置 STT 后端（幂等）。"""
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.STT_PROVIDER, "whisper") is not None:
        return

    def _call_whisper(service: Any, audio_file: str, language: str,
                      model_size: str, word_timestamps: bool) -> Any:
        return service._transcribe_whisper(audio_file, language, model_size, word_timestamps)

    def _call_faster(service: Any, audio_file: str, language: str,
                     model_size: str, word_timestamps: bool) -> Any:
        return service._transcribe_faster_whisper(
            audio_file, language, model_size, word_timestamps)

    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.STT_PROVIDER, "whisper", SttBackendEntry(
            name="whisper", is_available=_probe_whisper, call=_call_whisper,
        )),
        Contribution(ContributionKind.STT_PROVIDER, "faster-whisper", SttBackendEntry(
            name="faster-whisper", is_available=_probe_faster_whisper, call=_call_faster,
        )),
    ], dispatch=False)


#: 内置后端的尝试顺序（与旧硬编码一致；插件后端排在其后）
_BUILTIN_ORDER = ("whisper", "faster-whisper")


def resolve_stt_backends(service: Any) -> list[tuple[str, Callable[..., Any]]]:
    """返回有序的 (backend_name, callable) 列表供 `_transcribe_any` 遍历。

    门面不可用（极端环境）时返回空列表，由调用方回退旧顺序。
    """
    try:
        from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

        ensure_stt_providers_registered()
        entries = {c.id: c.object for _, c in ContributionRegistry.list(ContributionKind.STT_PROVIDER)}
        if not entries:
            return []
        ordered: list[tuple[str, Callable[..., Any]]] = []
        for name in _BUILTIN_ORDER:
            entry = entries.pop(name, None)
            if entry is not None:
                ordered.append((name, _bind(entry, service)))
        for name in sorted(entries):  # 插件贡献的 STT 后端（字典序，确定性）
            ordered.append((name, _bind(entries[name], service)))
        return ordered
    except Exception:  # noqa: BLE001 — 解析失败不阻断（调用方回退）
        return []


def _bind(entry: Any, service: Any) -> Callable[..., Any]:
    """把条目绑定到具体并返回 callable。"""
    if isinstance(entry, SttBackendEntry):
        return lambda *a, **k: entry.call(service, *a, **k)
    # 插件贡献：允许直接提供 transcribe(audio_file, **kwargs) 形态
    fn = getattr(entry, "transcribe", None)
    if callable(fn):
        return lambda audio_file, language, model_size, word_timestamps: fn(
            audio_file, language=language, model_size=model_size,
            word_timestamps=word_timestamps,
        )
    raise ValueError(f"STT provider 缺少可调用 transcribe: {entry!r}")
