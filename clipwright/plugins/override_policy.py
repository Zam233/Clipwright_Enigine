"""B7：`override_builtin` 的受控来源与允许目标表。

背景：42 份内置 manifest 当前都没有签名，其中 9 个声明
``override_builtin: true``。若直接把「未签名不得覆盖」启用，默认启动会失去
这 9 个能力；但仅凭 manifest 自报就授予 OVERRIDE 又等于没有授权。

策略（fail-closed，但保留已发布能力）：
1. **bundled 受信来源**：插件目录位于随应用发布的 ``<repo>/plugins``（由本
   文件路径推导，不读用户可配的 ``settings.plugin_dir``），且覆盖目标是下表中
   明确列出的 ``kind:id`` → 允许；
2. 其它来源（外部安装目录）：manifest HMAC 必须有效，且目标仍在允许表内；
3. 其余一律拒绝，错误可读。

manifest 自报 ``trusted: true`` 无效；本表按 kind:id 数据化，不允许按厂商名
写分支。bundled 信任只证明「随应用发布」，不防本机管理员篡改目录，那由内容
摘要/签名另行承担。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: 允许被 bundled 插件接管的内置贡献（当前 9 个插件实际覆盖的目标）。
#: 新增一项必须同时评审插件来源与内置去向，不能由插件自行扩权。
ALLOWED_BUILTIN_OVERRIDES: frozenset[str] = frozenset({
    "embedding_provider:openai",
    "embedding_provider:ollama",
    "embedding_provider:local",
    "llm_provider:anthropic",
    "llm_provider:openai",
    "llm_provider:ollama",
    "rerank_provider:http",
    "rerank_provider:local",
    "stt_provider:whisper",
    "stt_provider:faster-whisper",
    "tts_provider:cosyvoice",
    "tts_provider:minimax",
    "tts_provider:qwen-tts",
    "skill:dub_script",
})


def bundled_plugins_root() -> Path:
    """随应用发布的插件根目录（``<repo>/plugins``），与用户配置目录无关。"""
    return Path(__file__).resolve().parents[2] / "plugins"


def is_bundled_plugin(plugin_dir: str | Path | None) -> bool:
    """插件目录是否位于随应用发布的插件根下（realpath 比较）。"""
    if not plugin_dir:
        return False
    try:
        target = Path(plugin_dir).resolve()
        root = bundled_plugins_root().resolve()
    except (OSError, ValueError):
        return False
    return target == root or root in target.parents


def override_problems(
    manifest: Any,
    *,
    plugin_dir: str | Path | None,
    signature_ok: bool,
) -> list[str]:
    """返回拒绝 `override_builtin` 的原因列表（空 = 允许）。

    调用方必须在加载器完成签名校验后再调用；``signature_ok`` 为真表示
    manifest HMAC 已验证通过（无签名时为 False）。
    """
    if not bool(getattr(manifest, "override_builtin", False)):
        return []
    declared = [
        f"{str(getattr(d, 'kind', '') or '')}:{str(getattr(d, 'id', '') or '')}"
        for d in (getattr(manifest, "contributions", None) or [])
    ]
    unbundled = [key for key in declared if key not in ALLOWED_BUILTIN_OVERRIDES]
    if unbundled:
        return [
            "override_builtin 目标不在宿主允许表内: " + ", ".join(sorted(unbundled)),
        ]
    if is_bundled_plugin(plugin_dir):
        return []
    if not signature_ok:
        return [
            "第三方插件声明 override_builtin 但 manifest 签名无效；"
            "仅随应用发布的插件或无签名受信来源可覆盖内置贡献",
        ]
    return []


__all__ = [
    "ALLOWED_BUILTIN_OVERRIDES",
    "bundled_plugins_root",
    "is_bundled_plugin",
    "override_problems",
]
