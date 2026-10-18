"""类型规范的运行期适配（PRD Part I F2/F4、A-04/A-05）。

把「类型插件 → 规范（提示词/规则/指标/声明）→ 裁决结果 → 管线参数」这条链
放在一处，避免每个 Agent 各自伸手进 ``extra_params`` 并自行兜底。

设计要点：
- **零行为变化优先**：类型没有规则时，``resolve_shot_params`` 只做浅拷贝；
- 规则按 ``param_rules`` 生效（strict 覆盖用户值，soft 只记录，见 PromptAuthority）；
- 提供 ``stage_text`` 供各 Agent 注入"类型同一性/阶段规范"两段；
- 不碰 IO、不调 LLM，纯函数便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    SignatureTrait,
    TypeDeclares,
)
from clipwright.services.prompt_authority import PromptAuthority, PromptResolution


@dataclass
class TypeSpec:
    """一个类型的规范快照。"""

    category_id: str = ""
    prompts: PromptBundle = field(default_factory=PromptBundle)
    rules: list[ParamRule] = field(default_factory=list)
    traits: list[SignatureTrait] = field(default_factory=list)
    declares: TypeDeclares = field(default_factory=TypeDeclares)

    def authority(self, *, stage: str = "", platform_rules: Optional[list[ParamRule]] = None) -> PromptAuthority:
        return PromptAuthority(
            prompts=self.prompts,
            rules=self.rules,
            traits=self.traits,
            platform_rules=platform_rules,
            category_id=self.category_id,
            stage=stage,
        )


def load_spec(category_id: str) -> TypeSpec:
    """从 CategoryRegistry 取类型规范；类型不存在或插件不支持时返回空 spec。"""
    if not category_id:
        return TypeSpec()
    try:
        from clipwright.category.registry import CategoryRegistry

        plugin = CategoryRegistry.get(category_id)
    except Exception:  # pragma: no cover - 注册表不可用（极早期启动）
        return TypeSpec()
    if plugin is None:
        return TypeSpec(category_id=category_id)

    bundle = _call(plugin, "get_prompt_bundle", PromptBundle())
    rules = list(_call(plugin, "get_param_rules", []) or [])
    traits = list(_call(plugin, "get_signature_traits", []) or [])
    declares = _call(plugin, "get_declares", TypeDeclares())
    return TypeSpec(
        category_id=category_id,
        prompts=bundle if isinstance(bundle, PromptBundle) else PromptBundle(),
        rules=rules,
        traits=traits,
        declares=declares if isinstance(declares, TypeDeclares) else TypeDeclares(),
    )


def _call(plugin: Any, name: str, default: Any) -> Any:
    getter = getattr(plugin, name, None)
    if not callable(getter):
        return default
    try:
        value = getter()
    except Exception:  # noqa: BLE001 - 类型插件出错不应中断管线
        import logging

        logging.getLogger(__name__).exception("类型插件 %s.%s 调用失败", getattr(plugin, "plugin_id", "?"), name)
        return default
    return default if value is None else value


def resolve_shot_params(
    extra_params: dict[str, Any] | None,
    category_id: str,
) -> tuple[dict[str, Any], PromptResolution]:
    """按类型规则裁决镜头参数（A-05）。

    入参 ``extra_params["shot_params"]`` 视为"已合并的 L1/L3 剂量值"，
    这里只做 L2 规则的收口：strict 覆盖、soft 记录。
    返回 ``(合并后的 shot_params, 裁决结果)``。
    """
    extra = extra_params or {}
    shot = dict(extra.get("shot_params") or {})
    spec = load_spec(category_id)
    if not spec.rules:
        return shot, spec.authority().resolve(user=shot)
    resolution = spec.authority().resolve(user=shot)
    merged = {**shot, **{k: v for k, v in resolution.params.items() if "." not in k}}
    return merged, resolution


def resolve_params(
    extra_params: dict[str, Any] | None,
    category_id: str,
    *,
    stage: str = "",
    platform_rules: Optional[list[ParamRule]] = None,
) -> PromptResolution:
    """通用裁决：对 ``extra_params`` 顶层键应用类型规则与平台底线。"""
    spec = load_spec(category_id)
    return spec.authority(stage=stage, platform_rules=platform_rules).resolve(
        user=dict(extra_params or {})
    )


def stage_text(category_id: str, stage: str = "") -> str:
    """取该类型在某阶段应注入的文本（身份层 + 阶段层，固定顺序）。"""
    spec = load_spec(category_id)
    return spec.authority(stage=stage).stage_text()


def clamp_profile(
    profile: dict[str, Any],
    *,
    lo: Any = None,
    hi: Any = None,
) -> dict[str, Any]:
    """把 LLM 剪辑档案的 ``base_shot_ms`` 收敛到类型边界（纯函数，便于单测）。

    与 ``PromptAuthority`` 的分工：边界值来自裁决结果（已含 strict 规则），
    这里只做最后一道"LLM 越界值收敛"。
    """
    base = profile.get("base_shot_ms")
    if not isinstance(base, (int, float)) or isinstance(base, bool):
        return profile
    value = int(base)
    if isinstance(lo, (int, float)) and not isinstance(lo, bool) and lo > 0:
        value = max(value, int(lo))
    if isinstance(hi, (int, float)) and not isinstance(hi, bool) and hi > 0:
        value = min(value, int(hi))
    if value != base:
        profile = {**profile, "base_shot_ms": value}
    return profile


def bounds_of(shot_params: dict[str, Any] | None) -> tuple[Any, Any]:
    """取 ``(min_shot_ms, max_shot_ms)``；缺失返回 ``(None, None)``。"""
    shot = shot_params or {}
    return shot.get("min_shot_ms"), shot.get("max_shot_ms")


# ── A-08：类型声明（declares）取代 Agent 里的硬编码集合 ────────────────────


def declare_flag(category_id: str, field: str, legacy_default: bool) -> bool:
    """读 ``declares`` 的布尔声明；未声明时回落到旧硬编码判断。

    ``legacy_default`` 就是旧实现里那个 ``category_id in {...}`` 的结论——保留它
    一个发布周期做兜底（PRD D4），并在首次回落时打 deprecation 日志。
    """
    spec = load_spec(category_id)
    value = getattr(spec.declares, field, None)
    if value is None:
        if legacy_default:
            _log_legacy(field, category_id)
        return bool(legacy_default)
    return bool(value)


def required_markers(category_id: str) -> list[str]:
    """本类型必须落位的动画标记（如 ``data_chart``）。"""
    return list(load_spec(category_id).declares.required_animation_markers or [])


def requires_marker(category_id: str, marker: str, legacy_default: bool = False) -> bool:
    markers = required_markers(category_id)
    if markers:
        return marker in markers
    return declare_flag(category_id, "semantic_qa_default", legacy_default) and False


def search_hint(category_id: str, legacy_default: str = "") -> str:
    """本类型的选片/搜图规范（取代 ``_CATEGORY_SEARCH_HINTS`` 字典）。"""
    spec = load_spec(category_id)
    hint = (spec.declares.search_hint or "").strip()
    if hint:
        return hint
    if legacy_default:
        _log_legacy("search_hint", category_id)
    return legacy_default


_LEGACY_LOGGED: set[tuple[str, str]] = set()


def _log_legacy(field: str, category_id: str) -> None:
    """同一 (字段, 类型) 只记一次，避免刷屏。"""
    key = (field, category_id or "<none>")
    if key in _LEGACY_LOGGED:
        return
    _LEGACY_LOGGED.add(key)
    import logging

    logging.getLogger(__name__).info(
        "类型 %s 未声明 %s，暂时回落到旧硬编码判断（D4：本期需迁出到 declares）",
        category_id or "<none>",
        field,
    )


__all__ = [
    "TypeSpec",
    "bounds_of",
    "clamp_profile",
    "declare_flag",
    "load_spec",
    "required_markers",
    "requires_marker",
    "resolve_params",
    "resolve_shot_params",
    "search_hint",
    "stage_text",
]
