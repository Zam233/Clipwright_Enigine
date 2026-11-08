"""Provider 贡献的声明校验与 schema 登记（PRD §8「Provider manifest」）。

**为什么需要这个模块**

`ContributionDecl` 原来只有 `{kind, id, meta}` 三个字段，`meta` 是无类型口袋。
provider 类贡献需要两样**结构化**东西：

1. `config_schema`：驱动前端配置表单（哪些字段、什么类型、哪个是密钥）；
2. `capabilities`：声明协议能力（models / stream / tools / reasoning / usage /
   needs_uploader），供 `resolve/explain` 与调度判断。

塞进 `meta` 也能跑，但校验只能靠口头约定。所以 B1-T2 的结论是：

- **升一级字段**（`config_schema` / `capabilities`）放结构化数据；
- `meta` 继续承载 kind 专属的零散键（如 hook 的 `point`、ui_slot 的 `slot`）；
- 两者都**向后兼容**（默认空），老 manifest 不需要改。

**校验时机**：插件加载注册贡献时（`plugins/base.py::_register_contributions`）。
校验不通过 → 该条声明被**丢弃**（不是整插件失败），于是运行时注册会被
「未在 manifest 声明」拒绝——错误照样可见，但不会连累同插件其它贡献。
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "PROVIDER_KINDS",
    "PROVIDER_CAPABILITY_KEYS",
    "register_declaration",
    "forget_plugin",
    "schemas_for_kind",
    "validate_declaration",
]

#: provider 类贡献种类名（与 ContributionKind 的 value 对齐，用字符串避免循环导入）
PROVIDER_KINDS: frozenset[str] = frozenset({
    "llm_provider",
    "tts_provider",
    "stt_provider",
    "embedding_provider",
    "rerank_provider",
    "renderer_backend",
})

#: 协议能力键（PRD §8；未知键不报错，只做记录以便后续收敛）
PROVIDER_CAPABILITY_KEYS: frozenset[str] = frozenset({
    "models",
    "stream",
    "tools",
    "reasoning",
    "usage",
    "needs_uploader",
})

#: (kind, id) → {"plugin_id", "config_schema", "capabilities", "unknown_capability_keys"}
_DECLARATIONS: dict[tuple[str, str], dict[str, Any]] = {}


def validate_declaration(decl: Any) -> list[str]:
    """校验单条 provider 声明；返回问题列表（空 = 通过）。

    只做「结构是否合法」的硬校验，不猜业务语义：
    - `config_schema` 必须是 dict，每个字段的定义必须是 dict；
    - 字段定义里的 `secret` 若出现必须是 bool（前端据此决定掩码与加密）；
    - `capabilities` 必须是 dict。
    """
    problems: list[str] = []
    kind = str(getattr(decl, "kind", "") or "")
    cid = str(getattr(decl, "id", "") or "")

    if kind not in PROVIDER_KINDS:
        return problems  # 非 provider 类不走本模块

    schema = getattr(decl, "config_schema", None)
    if schema is None:
        schema = {}
    if not isinstance(schema, dict):
        problems.append(f"{kind}:{cid} 的 config_schema 必须是 object")
    else:
        for field_name, spec in schema.items():
            if not isinstance(field_name, str) or not field_name:
                problems.append(f"{kind}:{cid} 的 config_schema 出现非法字段名: {field_name!r}")
                continue
            if not isinstance(spec, dict):
                problems.append(f"{kind}:{cid}.{field_name} 的字段定义必须是 object")
                continue
            if "secret" in spec and not isinstance(spec.get("secret"), bool):
                problems.append(f"{kind}:{cid}.{field_name}.secret 必须是 bool")

    caps = getattr(decl, "capabilities", None)
    if caps is not None and not isinstance(caps, dict):
        problems.append(f"{kind}:{cid} 的 capabilities 必须是 object")

    return problems


def register_declaration(plugin_id: str, decl: Any) -> list[str]:
    """登记一条 provider 声明；返回问题列表。

    有问题时**不登记**（调用方据此把它从「已声明」集合里剔除）。
    """
    problems = validate_declaration(decl)
    if problems:
        return problems

    kind = str(getattr(decl, "kind", "") or "")
    if kind not in PROVIDER_KINDS:
        return []

    cid = str(getattr(decl, "id", "") or "")
    caps = dict(getattr(decl, "capabilities", None) or {})
    _DECLARATIONS[(kind, cid)] = {
        "plugin_id": str(plugin_id or ""),
        "config_schema": dict(getattr(decl, "config_schema", None) or {}),
        "capabilities": caps,
        "unknown_capability_keys": sorted(set(caps) - PROVIDER_CAPABILITY_KEYS),
    }
    return []


def forget_plugin(plugin_id: str) -> None:
    """插件卸载时清掉它登记的 provider 声明。"""
    owner = str(plugin_id or "")
    for key in [k for k, v in _DECLARATIONS.items() if v.get("plugin_id") == owner]:
        _DECLARATIONS.pop(key, None)


def schemas_for_kind(kind: str) -> list[dict[str, Any]]:
    """某 kind 下所有**插件**声明的 provider schema（内置 provider 不在此列）。

    内置 provider 的 schema 由 `api/providers.py` 的既有字段推导，不重复登记。
    """
    k = str(kind or "")
    out: list[dict[str, Any]] = []
    for (decl_kind, cid), payload in sorted(_DECLARATIONS.items()):
        if decl_kind != k:
            continue
        out.append({"id": cid, **payload})
    return out


def all_schemas() -> dict[str, list[dict[str, Any]]]:
    """按 kind 汇总全部插件 provider 声明（供 API 一次性下发）。"""
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in sorted({k for k, _ in _DECLARATIONS}):
        result[kind] = schemas_for_kind(kind)
    return result


def declared_keys(decls: Iterable[Any]) -> list[str]:
    """把声明列表转成门面用的 ``kind:id`` 字符串（便于测试/日志）。"""
    return sorted(f"{getattr(d, 'kind', '')}:{getattr(d, 'id', '')}" for d in decls or [])
