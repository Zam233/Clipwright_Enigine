"""从 `ContributionRegistry` 读 `display_spec` 契约，并做形状归一。

**单一真相在插件**：白名单/上限/配方都由插件在 `meta` 里声明；本模块只负责
"把它读成不可变对象 + 报错可读"，不含任何具体产品（GenUI）的字面量。

为什么不缓存：注册表本身就是内存字典，`unregister_plugin` 时会被清理；再叠一层
缓存就得自己管失效（插件启停 / reload / 子进程代理回填），收益接近零、失效点却
多三个。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from clipwright.plugins.contribution import ContributionKind
from clipwright.plugins.contribution_dispatch import (
    DISPLAY_SPEC_LIMIT_KEYS,
    DISPLAY_SPEC_REQUIRED_KEYS,
    DISPLAY_SPEC_SOURCES,
    DISPLAY_SPEC_TRUST_VALUES,
)


@dataclass(frozen=True)
class Contract:
    """一份 `display_spec` 贡献的**已校验**视图（不可变）。

    `recipe` 是"卡片怎么由源数据拼出来"的声明式配方（由插件提供、引擎通用解释器
    执行），`trust` 决定模型给的 spec 要不要用：

    - ``sanitize``：模型给了就洗，洗空/没给则按配方生成；
    - ``recipe_only``：**忽略**模型给的 spec，只用配方（用于"用户确认对象不可被
      展示层编造"的面，例如规划书）。
    """

    spec_id: str
    version: int
    source: str
    components: tuple[str, ...]
    containers: tuple[str, ...]
    actions: tuple[str, ...]
    tones: tuple[str, ...]
    badge_tones: tuple[str, ...]
    text_sizes: tuple[str, ...]
    limits: Mapping[str, int]
    order: int
    trust: str
    recipe: Mapping[str, Any] | None = None
    owner: str = ""

    # ── 便捷读取 ─────────────────────────────────────────────
    def limit(self, key: str, default: int = 0) -> int:
        value = self.limits.get(key, default)
        return value if isinstance(value, int) else default

    @property
    def sanitize_model_spec(self) -> bool:
        """模型给的 spec 是否可用（``recipe_only`` 时一律不用）。"""
        return self.trust == "sanitize"


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in value) if isinstance(value, list) else ()


def normalize_contract(meta: Mapping[str, Any] | None, *, owner: str = "") -> tuple[Contract | None, list[str]]:
    """把一条 `display_spec` 的 meta 归一成 `Contract`；形状不对返回 ``(None, problems)``。

    与 `contribution_dispatch._validate_data_only` **同一套必填键**（常量从这里共享），
    注册期拒绝的同时消费方也有一道兜底——注册表可能被别处直接写入（测试 / 内置镜像）。
    """
    problems: list[str] = []
    if not isinstance(meta, Mapping):
        return None, ["契约 meta 必须是对象"]
    missing = [k for k in DISPLAY_SPEC_REQUIRED_KEYS if k not in meta]
    if missing:
        problems.append(f"缺少必填键: {', '.join(missing)}")
    limits_raw = meta.get("limits")
    if not isinstance(limits_raw, Mapping):
        problems.append("limits 必须是对象")
        limits_raw = {}
    else:
        missing_limits = [k for k in DISPLAY_SPEC_LIMIT_KEYS if k not in limits_raw]
        if missing_limits:
            problems.append(f"limits 缺少: {', '.join(missing_limits)}")
        bad = [k for k in DISPLAY_SPEC_LIMIT_KEYS
               if k in limits_raw and (not isinstance(limits_raw[k], int)
                                       or isinstance(limits_raw[k], bool)
                                       or limits_raw[k] <= 0)]
        if bad:
            problems.append(f"limits 必须为正整数: {', '.join(bad)}")
    trust = str(meta.get("trust") or "")
    if trust not in DISPLAY_SPEC_TRUST_VALUES:
        problems.append(f"trust 非法: {trust or '空'}")
    source = str(meta.get("source") or "")
    if source not in DISPLAY_SPEC_SOURCES:
        problems.append(f"source 非法: {source or '空'}")
    recipe = meta.get("recipe")
    if recipe is not None and not isinstance(recipe, Mapping):
        problems.append("recipe 必须是对象")
    if trust == "recipe_only" and not (isinstance(recipe, Mapping) and recipe.get("nodes")):
        problems.append("trust=recipe_only 必须提供 recipe.nodes")
    if problems:
        return None, problems
    return Contract(
        spec_id=str(meta.get("spec_id") or ""),
        version=int(meta.get("version") or 0),
        source=source,
        components=_as_str_tuple(meta.get("components")),
        containers=_as_str_tuple(meta.get("containers")),
        actions=_as_str_tuple(meta.get("actions")),
        tones=_as_str_tuple(meta.get("tones")),
        badge_tones=_as_str_tuple(meta.get("badge_tones")),
        text_sizes=_as_str_tuple(meta.get("text_sizes")),
        limits={k: int(limits_raw[k]) for k in DISPLAY_SPEC_LIMIT_KEYS},
        order=int(meta.get("order") or 0),
        trust=trust,
        recipe=dict(recipe) if isinstance(recipe, Mapping) else None,
        owner=owner,
    ), []


def list_contracts() -> list[Contract]:
    """列出当前已注册且形状合法的全部契约（按 `order` 升序，再按 spec_id）。

    插件被禁用/卸载后自然消失（注册表里没了）——这正是"能力归插件"的可观察点。
    """
    from clipwright.plugins.contribution import ContributionRegistry

    out: list[Contract] = []
    for owner, item in ContributionRegistry.list(ContributionKind.DISPLAY_SPEC):
        contract, problems = normalize_contract(item.meta, owner=owner)
        if contract is None:
            continue
        out.append(contract)
    out.sort(key=lambda c: (c.order, c.spec_id))
    return out


def get_contract(spec_id: str) -> Contract | None:
    """按 `spec_id` 取契约；不存在或形状不合法返回 ``None``。"""
    target = str(spec_id or "")
    if not target:
        return None
    for contract in list_contracts():
        if contract.spec_id == target:
            return contract
    return None
