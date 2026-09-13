"""贡献分发适配器（方案 A-1）—— 把门面注册转调到现有 7 个注册表。

设计（依据 §13.3 修正 1）：冲突判定（内置不可覆盖/插件互斥/声明即授权）
已由 `ContributionRegistry` 统一完成；本模块只做**转调**，不重复造冲突逻辑，
也不改变各注册表公开方法签名。

转调映射：
    tool               → ToolRegistry.register / unregister
    skill              → SkillRegistry.register / unregister
    agent              → AgentRegistry.register（deps 从 meta 读）
    material_source    → MaterialRegistry.register / unregister
    category           → CategoryRegistry.register / unregister_plugin
    style_interpreter  → StyleInterpreter.register_plugin / unregister_plugin
    hook               → HookRegistry.register(point=meta["point"], ...) / unregister_plugin
    prompt             → PluginPromptRegistry.register / unregister

其他 kind（B/D 新增）由各自落地模块在自己的里程碑接入（llm_provider 等），
在此之前 dispatch 对未知 kind 返回明确的「尚未接线」而非静默成功。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from clipwright.config import logger
from clipwright.plugins.contribution import Contribution, ContributionKind

if TYPE_CHECKING:  # pragma: no cover
    pass

#: 已在 dispatch 接线的 kind（其余 kind 由 B/D 里程碑在自己的注册表落地）
_WIRED_KINDS = {
    ContributionKind.TOOL,
    ContributionKind.SKILL,
    ContributionKind.AGENT,
    ContributionKind.MATERIAL_SOURCE,
    ContributionKind.CATEGORY,
    ContributionKind.STYLE_INTERPRETER,
    ContributionKind.HOOK,
    ContributionKind.PROMPT,
}

#: Provider 类 kind（facade-only，但注册前要做协议符合性校验 —— B2）
_PROVIDER_KINDS = {
    ContributionKind.LLM_PROVIDER,
    ContributionKind.EMBEDDING_PROVIDER,
    ContributionKind.RERANK_PROVIDER,
    ContributionKind.TTS_PROVIDER,
    ContributionKind.STT_PROVIDER,
    ContributionKind.RENDERER_BACKEND,
}


def register(owner: str, item: Contribution) -> Optional[str]:
    """把单个贡献转调到目标注册表。返回 None=成功，字符串=失败原因。

    B/D 新增的 kind（llm_provider / ui_slot 等）**没有后端注册表**——门面
    自己就是它们的注册表（消费方经 `ContributionRegistry.resolve/list` 读取），
    因此直接接受（facade-only）。**B2 例外**：provider 类贡献先做协议符合性
    校验，坏 provider 在注册时就被拒绝并给出可读原因（而不是运行期
    AttributeError）。
    """
    if item.kind in _PROVIDER_KINDS:
        if item.kind is ContributionKind.LLM_PROVIDER:
            from clipwright.providers.base import missing_llm_provider_methods
            missing = missing_llm_provider_methods(item.object)
            if missing:
                return (f"llm_provider:{item.id} 不符合协议，缺少方法: "
                        f"{', '.join(missing)}")
        return None

    if item.kind not in _WIRED_KINDS:
        return None  # facade-only：由各消费方（B/D 里程碑）自行解析

    try:
        if item.kind is ContributionKind.TOOL:
            from clipwright.tool.registry import ToolRegistry
            ToolRegistry.register(item.object, plugin_id=owner)
            # 后置校验：ToolRegistry 对「插件覆盖内置」是静默拒绝（warn+return，
            # 不抛异常）——不核对归属会让门面误记 owner 并在卸载时误删内置工具
            applied = ToolRegistry.get(item.id)
            if applied is None or getattr(applied, "_plugin_id", "") != owner:
                return f"tool:{item.id} 注册未被注册表接受（疑似与内置冲突）"

        elif item.kind is ContributionKind.SKILL:
            from clipwright.skill.registry import SkillRegistry
            SkillRegistry.register(item.object, plugin_id=owner)
            applied = SkillRegistry.get(item.id)
            if applied is None or str(getattr(applied, "_plugin_id", "")) != owner:
                return f"skill:{item.id} 注册未被注册表接受（疑似与内置冲突）"

        elif item.kind is ContributionKind.AGENT:
            from clipwright.agents.registry import AgentRegistry
            AgentRegistry.register(
                item.object,
                name=item.id,
                plugin_id=owner,
                deps=list(item.meta.get("deps") or []),
                description=str(item.meta.get("description", "")),
            )

        elif item.kind is ContributionKind.MATERIAL_SOURCE:
            from clipwright.material.registry import MaterialRegistry
            MaterialRegistry.register(item.object, plugin_id=owner)

        elif item.kind is ContributionKind.CATEGORY:
            from clipwright.category.registry import CategoryRegistry
            CategoryRegistry.register(item.object)
            CategoryRegistry.set_owner(item.id, owner)

        elif item.kind is ContributionKind.STYLE_INTERPRETER:
            from clipwright.services.style_interpreter import StyleInterpreter
            StyleInterpreter.register_plugin(item.object, owner=owner)

        elif item.kind is ContributionKind.HOOK:
            from clipwright.plugins.hooks import HookPoint, HookRegistry
            point = item.meta.get("point")
            if point is None:
                return f"hook 贡献缺少 meta.point: {item.id}"
            if isinstance(point, str):
                try:
                    point = HookPoint[point.upper()]
                except KeyError:
                    return f"未知 hook point: {item.meta.get('point')}"
            HookRegistry.register(point, item.object, plugin_id=owner)

        elif item.kind is ContributionKind.PROMPT:
            from clipwright.plugins.prompt_registry import PluginPromptRegistry
            agent_name = str(item.meta.get("agent") or item.meta.get("agent_name") or "")
            prompt_text = str(item.meta.get("prompt") or "")
            if not agent_name or not prompt_text:
                return f"prompt 贡献缺少 meta.agent / meta.prompt: {item.id}"
            PluginPromptRegistry.register(
                owner, agent_name, prompt_text,
                priority=int(item.meta.get("priority", 0) or 0),
                description=str(item.meta.get("description", "")),
            )
        else:  # pragma: no cover — _WIRED_KINDS 已穷举
            return f"未实现的贡献类型: {item.kind.value}"
    except ValueError as e:
        # AgentRegistry 的非法名/重复注册等以 ValueError 表达
        return f"{item.kind.value}:{item.id} 注册失败: {e}"
    except Exception as e:  # noqa: BLE001 — 单条失败不该中断批量注册
        logger.warning("贡献转调失败 %s:%s: %s", item.kind.value, item.id, e)
        return f"{item.kind.value}:{item.id} 注册异常: {e}"
    return None


def unregister(owner: str, keys: list[str]) -> list[str]:
    """回收某 owner 的全部贡献（门面已按 owner 聚合，一次性调用）。

    ``keys`` 为 ``kind:id`` 形态；按 kind 分组后优先走「按 owner 批量回收」
    的注册表方法（Hook/Agent/Category），其余按名单条回收。
    """
    by_kind: dict[str, list[str]] = {}
    for key in keys:
        kind, _, cid = key.partition(":")
        by_kind.setdefault(kind, []).append(cid)

    recycled: list[str] = []
    for kind_value, ids in by_kind.items():
        try:
            if kind_value == ContributionKind.TOOL.value:
                from clipwright.tool.registry import ToolRegistry
                for cid in ids:
                    ToolRegistry.unregister(cid)
            elif kind_value == ContributionKind.SKILL.value:
                from clipwright.skill.registry import SkillRegistry
                for cid in ids:
                    SkillRegistry.unregister(cid)
            elif kind_value == ContributionKind.AGENT.value:
                from clipwright.agents.registry import AgentRegistry
                AgentRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.MATERIAL_SOURCE.value:
                from clipwright.material.registry import MaterialRegistry
                for cid in ids:
                    MaterialRegistry.unregister(cid)
            elif kind_value == ContributionKind.CATEGORY.value:
                from clipwright.category.registry import CategoryRegistry
                CategoryRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.STYLE_INTERPRETER.value:
                from clipwright.services.style_interpreter import StyleInterpreter
                StyleInterpreter.unregister_plugin(owner)
            elif kind_value == ContributionKind.HOOK.value:
                from clipwright.plugins.hooks import HookRegistry
                HookRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.PROMPT.value:
                from clipwright.plugins.prompt_registry import PluginPromptRegistry
                PluginPromptRegistry.unregister(owner)
            else:
                # 未接线的 kind 只从门面移除（本期无注册表绑定）
                pass
            recycled.extend(f"{kind_value}:{cid}" for cid in ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("贡献回收失败 %s (owner=%s): %s", kind_value, owner, e)
    return recycled
