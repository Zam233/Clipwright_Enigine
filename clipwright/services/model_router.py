"""模型路由（PRD F8 / 开发计划 B4）。

三条硬约束（与 PRD §6.3 / §6.5 对齐）：

1. **白名单**：只能选 `provider + model` 组合，且 provider 必须是已配置的来源
   （`provider_sources` 里的 llm 源）或主/闪模型 provider。白名单由
   `allowed_providers()` 统一计算——**命令风险判定（B9）与子代理审核共用同一份**，
   不另开通道。
2. **只按预算与历史收缩**：预算余量不足时只允许降价；同 (stage, provider/model)
   已失败则跳过。
3. **不改默认行为**：路由表为空 / 无匹配时返回 None，调用方沿用原模型——这样
   `model_routes` 留空时系统行为与改造前完全一致。

运行时通过 `use_route()` 上下文把选中的 route 暴露给 `LLMService`，仍保持
「单点埋设」：6 个 Agent 内部零改动。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

from clipwright.config import logger, settings

TIERS: tuple[str, ...] = ("cheap", "std", "strong")

#: 各阶段偏好的档位顺序（PRD §6.2 任务类型信号）
_STAGE_TIER_PREF: dict[str, tuple[str, ...]] = {
    "structure": ("std", "strong", "cheap"),
    "material": ("cheap", "std", "strong"),
    "edit": ("std", "strong", "cheap"),
    "animation": ("std", "strong", "cheap"),
    "audio": ("cheap", "std", "strong"),
    "quality": ("cheap", "std"),
}
_DEFAULT_PREF: tuple[str, ...] = ("std", "cheap", "strong")


@dataclass(frozen=True)
class ModelRoute:
    """一个可选的模型路由（provider + model + 档位与价格）。"""

    provider: str
    model: str
    tier: str = "std"
    price_in: float = 0.0
    price_out: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "price_in": self.price_in,
            "price_out": self.price_out,
        }


# ── 白名单（与 B9 的 risk_judge 共用）─────────────────────────


def allowed_providers() -> set[str]:
    """可用 provider 白名单：主/闪模型 provider + 已配置的 llm 源 + 显式白名单。"""
    out: set[str] = set()
    try:
        if settings.llm_provider:
            out.add(str(settings.llm_provider))
        if getattr(settings, "llm_flash_provider", None):
            out.add(str(settings.llm_flash_provider))
    except Exception:  # noqa: BLE001
        pass
    try:
        from clipwright.services import provider_sources as _ps

        for src in _ps.list_sources(kind="llm", public=True):
            prov = str(src.get("provider") or "")
            if prov:
                out.add(prov)
    except Exception as e:  # noqa: BLE001 — 源存储不可用不影响主流程
        logger.debug("provider_sources 读取失败（白名单仅含 settings）: %s", e)
    for item in _explicit_whitelist():
        prov = item.split("/", 1)[0].strip()
        if prov:
            out.add(prov)
    return out


def _explicit_whitelist() -> list[str]:
    raw = getattr(settings, "model_whitelist", None)
    if isinstance(raw, str):  # 环境变量形态："a/b,c/d"
        raw = [p.strip() for p in raw.split(",") if p.strip()]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def is_route_allowed(route: ModelRoute) -> bool:
    """白名单判定：显式白名单非空时按 provider/model 精确匹配。"""
    explicit = _explicit_whitelist()
    if explicit:
        return route.key in explicit or route.model in explicit
    return route.provider in allowed_providers()


def resolve_provider_credentials(provider: str) -> Optional[dict[str, Any]]:
    """解析某个 provider 的运行时凭据（供跨 provider 调用）。

    顺序：角色绑定（main/flash）→ 同 provider 的 llm 源 → settings。
    任何一步失败都回退到 None（调用方沿用原 provider，绝不静默串用凭据）。
    """
    if not provider:
        return None
    try:
        from clipwright.services import provider_sources as _ps

        for role in ("main", "flash"):
            bound = _ps.resolve(role)
            if bound and str(bound.get("provider") or "") == provider:
                return bound
        for src in _ps.list_sources(kind="llm", public=True):
            if str(src.get("provider") or "") != provider:
                continue
            resolved = _ps.resolve_for_source(str(src.get("id") or ""))
            if resolved:
                return resolved
    except Exception as e:  # noqa: BLE001
        logger.debug("provider 凭据解析失败（%s）: %s", provider, e)
    try:
        if provider == str(settings.llm_provider):
            return {
                "provider": provider,
                "api_key": settings.llm_api_key or None,
                "base_url": settings.llm_base_url,
                "model": settings.llm_model,
            }
    except Exception:  # noqa: BLE001
        return None
    return None


# ── 路由表与选择 ────────────────────────────────────────────


def route_table(stage: str = "") -> list[ModelRoute]:
    """把 `settings.model_routes` 解析成 ModelRoute 列表（可只取某阶段）。"""
    raw = getattr(settings, "model_routes", None) or {}
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            raw = {}
    if not isinstance(raw, dict):
        return []
    stages = [stage] if stage else list(raw.keys())
    out: list[ModelRoute] = []
    for st in stages:
        items = raw.get(st) or []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or settings.llm_provider or "")
            model = str(item.get("model") or "")
            if not provider or not model:
                continue
            tier = str(item.get("tier") or "std").lower()
            out.append(ModelRoute(
                provider=provider,
                model=model,
                tier=tier if tier in TIERS else "std",
                price_in=float(item.get("price_in") or item.get("price_in_per_1k") or 0.0),
                price_out=float(item.get("price_out") or item.get("price_out_per_1k") or 0.0),
            ))
    return out


def choose_route(
    stage: str,
    *,
    failed_pairs: Iterable[str] = (),
    budget_ratio: Optional[float] = None,
) -> Optional[ModelRoute]:
    """为某阶段挑一个模型；无可用候选时返回 None（调用方沿用默认模型）。

    `budget_ratio`：已用预算占比（0~1）。>=0.7 时只允许 `cheap/std`，不给升级。
    """
    candidates = [r for r in route_table(stage) if is_route_allowed(r)]
    if not candidates:
        return None

    failed = {str(x) for x in failed_pairs}
    candidates = [r for r in candidates if r.key not in failed and r.model not in failed]
    if not candidates:
        return None

    if budget_ratio is not None and budget_ratio >= 0.7:
        narrowed = [r for r in candidates if r.tier in ("cheap", "std")]
        candidates = narrowed or candidates

    pref = _STAGE_TIER_PREF.get(stage, _DEFAULT_PREF)
    rank = {t: i for i, t in enumerate(pref)}
    candidates.sort(key=lambda r: (rank.get(r.tier, len(pref)), r.price_in + r.price_out))
    return candidates[0]


def risk_judge_route() -> Optional[ModelRoute]:
    """命令风险判定要用的模型（PRD F11 / B9）。

    优先级：显式 `settings.risk_judge_model`（必须命中白名单）→ 全表最便宜的
    `cheap` 档。与主代理模型解耦，但**共用同一份 provider 白名单**。
    """
    raw = str(getattr(settings, "risk_judge_model", "") or "").strip()
    if raw:
        if "/" in raw:
            provider, model = raw.split("/", 1)
        else:
            provider, model = str(settings.llm_provider or ""), raw
        route = ModelRoute(provider=provider, model=model, tier="cheap")
        if not is_route_allowed(route):
            logger.warning("risk_judge_model 未命中 provider 白名单，忽略: %s", raw)
            return None
        return route
    all_routes = [r for r in route_table() if is_route_allowed(r) and r.tier == "cheap"]
    if not all_routes:
        return None
    all_routes.sort(key=lambda r: r.price_in + r.price_out)
    return all_routes[0]


# ── 运行时上下文（单点埋设）──────────────────────────────────

_ACTIVE: ContextVar[Optional[ModelRoute]] = ContextVar("active_model_route", default=None)


@contextmanager
def use_route(route: Optional[ModelRoute]) -> Iterator[Optional[ModelRoute]]:
    """在作用域内启用某个路由（None = 沿用默认模型）。"""
    token = _ACTIVE.set(route)
    try:
        yield route
    finally:
        _ACTIVE.reset(token)


def active_route() -> Optional[ModelRoute]:
    return _ACTIVE.get()


def route_summary(route: Optional[ModelRoute]) -> str:
    return route.key if route else "default"
