"""subprocess 插件 Category 的宿主适配（M4）。

静态部分（PromptBundle / rules / traits / declares / 展示信息）由插件在
`clip_kind` 同级的 `category` 贡献 meta 里声明，engine 据此构造一个普通
`BaseCategoryPlugin`；需要执行逻辑的 `translate_persona` / `get_shot_params`
通过 `category.*` RPC 转发到 worker。

engine 不解析插件私有工程文件；这里只承载类型提示词与像素无关的参数翻译。
"""
from __future__ import annotations

from typing import Any

from clipwright.category.base import BaseCategoryPlugin
from clipwright.config import logger
from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
)
from clipwright.schema.plugin import PluginManifest


class RemoteCategoryPlugin(BaseCategoryPlugin):
    """把 subprocess 插件声明出来的 Category 呈现为宿主类型插件。"""

    def __init__(
        self, host: Any, plugin_id: str, meta: dict[str, Any],
        category_id: str = "",
    ) -> None:
        self._host = host
        self._plugin_id = str(plugin_id)
        # 类型 id 用贡献 id（CategoryRegistry 的键）；RPC 的贡献 id 也用它
        self._contrib_id = str(category_id or plugin_id)
        self.plugin_id = str(meta.get("plugin_id") or self._contrib_id)
        self.display_name = str(meta.get("display_name") or plugin_id)
        self.description = str(meta.get("description") or "")
        self.manifest = PluginManifest(
            id=self._plugin_id, name=self.display_name, kind="category",
        )
        self._prompts = _build_prompts(meta.get("prompts") or {})
        self._rules = _build_models(ParamRule, meta.get("rules") or [])
        self._traits = _build_models(SignatureTrait, meta.get("traits") or [])
        try:
            self._declares = TypeDeclares(**(meta.get("declares") or {}))
        except Exception:  # noqa: BLE001 — 声明坏掉不应中断注册
            self._declares = TypeDeclares()

    # ── 静态声明 ──────────────────────────────────────
    def get_prompt_bundle(self) -> PromptBundle:
        return self._prompts

    def get_param_rules(self) -> list[ParamRule]:
        return list(self._rules)

    def get_signature_traits(self) -> list[SignatureTrait]:
        return list(self._traits)

    def get_declares(self) -> TypeDeclares:
        return self._declares

    # ── 可执行部分：走 RPC ────────────────────────────
    def translate_persona(self, params: Any) -> dict[str, Any]:
        payload = params.model_dump() if hasattr(params, "model_dump") else dict(params or {})
        return self._rpc("category.translate_persona", {"params": payload}) or {}

    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("category.get_shot_params", {"translated": translated}) or {}

    def _rpc(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._host is None:
            return {}
        try:
            result = self._host.invoke_sync(
                method,
                {"id": self._contrib_id, "payload": payload},
                timeout=30.0,
            )
            return result if isinstance(result, dict) else {}
        except Exception as e:  # noqa: BLE001 — 插件方法失败时回退默认值并留痕
            logger.warning("远程 Category 方法失败 %s（%s）: %s",
                           method, self._plugin_id, e)
            return {}


def _build_prompts(raw: dict[str, Any]) -> PromptBundle:
    slots: dict[str, PromptSlot] = {}
    for key, text in dict(raw or {}).items():
        if isinstance(text, str) and text.strip():
            slots[str(key)] = PromptSlot(text=text)
    return PromptBundle(slots=slots).normalized()


def _build_models(model: Any, raw: list[Any]) -> list[Any]:
    out: list[Any] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model(**item))
        except Exception:  # noqa: BLE001 — 单条声明非法就跳过
            logger.warning("忽略非法 %s 声明: %s", model.__name__, item)
    return out


__all__ = ["RemoteCategoryPlugin"]
