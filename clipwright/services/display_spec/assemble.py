"""通用装配器：按 `display_spec` 契约把展示 spec 补进业务 payload。

这是 engine 侧**唯一**的写入机制（能力归插件，机制归宿主）：

1. 取该 `source`（`brief` / `plan`）对应的全部契约，按 `order` 升序；
2. **没有契约 = 合法状态**（插件未安装/被禁用）：payload 原样返回，只记 info 日志；
3. `trust=recipe_only` 的契约：忽略模型给的 spec，只用插件配方；
   `trust=sanitize`：先洗模型给的，洗空/没给才用配方；
4. 只写 `payload["display_specs"]` 这一个键，不碰任何业务字段（C10）；
5. 任何异常都不阻断业务写入（展示层坏掉不该让人写不进简报），只记 warning + 跳过该契约。
"""
from __future__ import annotations

from typing import Any

from clipwright.config import logger

from .contract import Contract, list_contracts
from .recipe import build_spec_from_recipe
from .sanitizer import sanitize_display_spec

#: 展示层容器键（与 `_brief_hash` 的排除集合保持一致）。
DISPLAY_SPECS_KEY = "display_specs"


def _contracts_for(source: str) -> list[Contract]:
    return [c for c in list_contracts() if c.source == source]


def ensure_display_specs(
    source: str,
    payload: dict[str, Any],
    *,
    scenes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把 `source` 面所有契约的 spec 写进 `payload[DISPLAY_SPECS_KEY]`。

    返回同一个 payload 对象（就地写一个键）；`source` 没有契约时**原样返回**，
    不创建空容器（避免在插件未装时凭空多出一个键）。
    """
    if not isinstance(payload, dict):
        return payload
    contracts = _contracts_for(source)
    if not contracts:
        logger.info("display_spec: 没有声明 source=%s 的契约（插件未装/被禁用），payload 不变", source)
        return payload

    view = dict(payload)
    if scenes is not None:
        view["raw_scenes"] = list(scenes)
    container = payload.get(DISPLAY_SPECS_KEY)
    if not isinstance(container, dict):
        container = {}
        payload[DISPLAY_SPECS_KEY] = container

    for contract in contracts:
        try:
            spec: dict[str, Any] | None = None
            if contract.sanitize_model_spec:
                raw = container.get(contract.spec_id)
                if raw is not None:
                    spec, _problems = sanitize_display_spec(raw, contract, source=source)
            if spec is None and contract.recipe:
                spec = build_spec_from_recipe(
                    contract.recipe, view, contract,
                    title=str(contract.recipe.get("title") or ""),
                )
            if spec is None:
                container.pop(contract.spec_id, None)
            else:
                container[contract.spec_id] = spec
        except Exception as exc:  # noqa: BLE001 — 展示层异常不得阻断业务写入
            logger.warning(
                "display_spec: 装配 %s(%s) 失败，已跳过：%s", contract.spec_id, source, exc
            )
            continue

    if not container:
        payload.pop(DISPLAY_SPECS_KEY, None)
    return payload
