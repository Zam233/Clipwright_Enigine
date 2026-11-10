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
    animation          → AnimationRegistry.register / unregister_plugin
    style_interpreter  → StyleInterpreter.register_plugin（按 owner 多注册，meta.priority 定活跃项）/ unregister_plugin
    hook               → HookRegistry.register(point=meta["point"], ...) / unregister_plugin
    prompt             → PluginPromptRegistry.register / unregister

其他 kind（B/D 新增）由各自落地模块在自己的里程碑接入（llm_provider 等），
在此之前 dispatch 对未知 kind 返回明确的「尚未接线」而非静默成功。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from clipwright.config import logger
from clipwright.plugins.contribution import ConflictPolicy, Contribution, ContributionKind

if TYPE_CHECKING:  # pragma: no cover
    pass

#: 已在 dispatch 接线的 kind（其余 kind 由 B/D 里程碑在自己的注册表落地）
_WIRED_KINDS = {
    ContributionKind.TOOL,
    ContributionKind.SKILL,
    ContributionKind.AGENT,
    ContributionKind.MATERIAL_SOURCE,
    ContributionKind.CATEGORY,
    ContributionKind.ANIMATION,
    ContributionKind.STYLE_INTERPRETER,
    ContributionKind.HOOK,
    ContributionKind.PROMPT,
    ContributionKind.CLIP_KIND,
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

#: **纯数据** kind（2026-11-07 第 18 类 display_spec）：`object` 恒为 `None`，
#: 全部信息在 `meta`。engine 从不 `callable()` 插件的任何对象（PRD §3.3 零信任）。
_DATA_ONLY_KINDS = {ContributionKind.DISPLAY_SPEC, ContributionKind.EXPORT_PRESET}

#: display_spec 的 meta 必填键（与 `services/display_spec/contract.py` 共用同一份真相）。
DISPLAY_SPEC_REQUIRED_KEYS = (
    "spec_id", "version", "source", "components", "containers", "actions",
    "tones", "badge_tones", "text_sizes", "limits", "order", "trust",
)
#: limits 必须齐全的 9 个键（正整数）。
DISPLAY_SPEC_LIMIT_KEYS = (
    "max_nodes", "max_depth", "max_text_chars", "max_spec_bytes",
    "max_array_items", "max_table_cols", "max_action_payload_chars",
    "max_select_options", "max_option_chars",
)
DISPLAY_SPEC_TRUST_VALUES = ("sanitize", "recipe_only")
#: engine 侧的装配器只有这两个（新增装配器要改 `services/display_spec/assemble.py`）。
DISPLAY_SPEC_SOURCES = ("brief", "plan")


def _validate_export_preset(item: Contribution) -> Optional[str]:
    """导出预设的纯数据校验：宽高帧率码率必须是能直接喂给渲染器的形状。"""
    meta = item.meta or {}
    for key in ("width", "height", "fps"):
        value = meta.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            return f"export_preset:{item.id} 的 {key} 必须是正数"
    bitrate = meta.get("bitrate")
    if not isinstance(bitrate, str) or not bitrate.strip():
        return f"export_preset:{item.id} 的 bitrate 必须是非空字符串（如 6M / cq26）"
    thumb = meta.get("thumbnail_count", 0)
    if thumb not in ("", None) and (not isinstance(thumb, int) or isinstance(thumb, bool) or thumb < 0):
        return f"export_preset:{item.id} 的 thumbnail_count 必须是非负整数"
    return None


def _validate_data_only(item: Contribution) -> Optional[str]:
    """校验纯数据贡献的形状；返回 None=接受，字符串=可读中文原因。

    为什么必须在注册期校验：形状错的契约如果放行，会一路静默到渲染期才炸
    （或更糟——静默生效到用户看到空卡片）。因此**不能**靠「未知 kind 默认放行」。
    """
    if item.object is not None:
        return f"{item.kind.value}:{item.id} 的 object 必须为 None（纯数据扩展点）"
    if item.kind is ContributionKind.EXPORT_PRESET:
        return _validate_export_preset(item)
    meta = item.meta if isinstance(item.meta, dict) else None
    if meta is None:
        return f"display_spec:{item.id} 的 meta 必须是对象"
    try:
        json.dumps(meta, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return f"display_spec:{item.id} 的 meta 不可 JSON 序列化: {exc}"

    missing = [k for k in DISPLAY_SPEC_REQUIRED_KEYS if k not in meta]
    if missing:
        return f"display_spec:{item.id} 缺少必填键: {', '.join(missing)}"
    spec_id = str(meta.get("spec_id") or "")
    if not spec_id or spec_id != item.id:
        return (f"display_spec:{item.id} 的 meta.spec_id 必须与贡献 id 相同"
                f"（实际 {spec_id or '空'}）")

    trust = str(meta.get("trust") or "")
    if trust not in DISPLAY_SPEC_TRUST_VALUES:
        return (f"display_spec:{item.id} 的 trust 非法: {trust or '空'}"
                f"（允许 {'/'.join(DISPLAY_SPEC_TRUST_VALUES)}）")
    source = str(meta.get("source") or "")
    if source not in DISPLAY_SPEC_SOURCES:
        return (f"display_spec:{item.id} 的 source 非法: {source or '空'}"
                f"（允许 {'/'.join(DISPLAY_SPEC_SOURCES)}）")

    for key in ("version", "order"):
        value = meta.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"display_spec:{item.id} 的 {key} 必须是整数"
    for key in ("components", "containers", "actions", "tones", "badge_tones", "text_sizes"):
        value = meta.get(key)
        if not isinstance(value, list) or not value:
            return f"display_spec:{item.id} 的 {key} 必须是非空数组"
        if any(not isinstance(x, str) or not x.strip() for x in value):
            return f"display_spec:{item.id} 的 {key} 只能是非空字符串"
    unknown_containers = [c for c in meta["containers"] if c not in meta["components"]]
    if unknown_containers:
        return (f"display_spec:{item.id} 的 containers 不在 components 里: "
                f"{', '.join(unknown_containers)}")

    limits = meta.get("limits")
    if not isinstance(limits, dict):
        return f"display_spec:{item.id} 的 limits 必须是对象"
    missing_limits = [k for k in DISPLAY_SPEC_LIMIT_KEYS if k not in limits]
    if missing_limits:
        return f"display_spec:{item.id} 的 limits 缺少: {', '.join(missing_limits)}"
    for key in DISPLAY_SPEC_LIMIT_KEYS:
        value = limits[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return f"display_spec:{item.id} 的 limits.{key} 必须是正整数"

    recipe = meta.get("recipe")
    if trust == "recipe_only" and not (isinstance(recipe, dict) and recipe.get("nodes")):
        return f"display_spec:{item.id} 在 trust=recipe_only 下必须提供 recipe.nodes"
    if recipe is not None and not isinstance(recipe, dict):
        return f"display_spec:{item.id} 的 recipe 必须是对象"
    return None


def register(owner: str, item: Contribution, *, policy=None) -> Optional[str]:
    """把单个贡献转调到目标注册表。返回 None=成功，字符串=失败原因。

    B/D 新增的 kind（llm_provider / ui_slot 等）**没有后端注册表**——门面
    自己就是它们的注册表（消费方经 `ContributionRegistry.resolve/list` 读取），
    因此直接接受（facade-only）。**B2 例外**：provider 类贡献先做协议符合性
    校验，坏 provider 在注册时就被拒绝并给出可读原因（而不是运行期
    AttributeError）。
    """
    if item.kind in _DATA_ONLY_KINDS:
        # 纯数据扩展点：先拒 override（展示契约被替换 = 悄悄改变信任边界，PRD §3.1 N2），
        # 再做形状校验（不得靠「未知 kind 默认放行」，PRD §3.1 N5）。
        if policy is ConflictPolicy.OVERRIDE:
            return f"display_spec:{item.id} 不允许 override（展示契约不得被替换）"
        return _validate_data_only(item)

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
            from clipwright.plugins.contribution import ConflictPolicy as _CP
            SkillRegistry.register(item.object, plugin_id=owner,
                                   allow_override=(policy is _CP.OVERRIDE))
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

        elif item.kind is ContributionKind.ANIMATION:
            from clipwright.animation.registry import AnimationRegistry
            AnimationRegistry.register(item.object, plugin_id=owner)
            # 后置校验：AnimationRegistry.register 对空 animation_id 才抛错，
            # 这里确认归属真的写成 owner（否则卸载会漏收）
            owned = [a for a in AnimationRegistry.list_by_plugin(owner)
                     if getattr(a, "animation_id", "") == item.id]
            if not owned:
                return f"animation:{item.id} 注册未被注册表接受"

        elif item.kind is ContributionKind.STYLE_INTERPRETER:
            from clipwright.services.style_interpreter import StyleInterpreter
            # D3：多注册（每个 owner 一个槽位）；meta.priority 决定活跃项
            StyleInterpreter.register_plugin(
                item.object, owner=owner, priority=int(item.meta.get("priority", 100) or 100),
            )

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

        elif item.kind is ContributionKind.CLIP_KIND:
            from clipwright.services.clip_kind_registry import (
                ClipKindDefinition,
                ClipKindRegistry,
            )

            meta = dict(item.meta or {})
            ClipKindRegistry.register(ClipKindDefinition(
                kind_id=item.id,
                display_name=str(meta.get("display_name") or ""),
                track_label=str(meta.get("track_label") or ""),
                icon=str(meta.get("icon") or ""),
                metadata_schema=dict(meta.get("metadata_schema") or {}),
                render_contract=str(meta.get("render_contract") or ""),
                preview_contract=str(meta.get("preview_contract") or ""),
                renderer_tool=str(meta.get("renderer_tool") or ""),
                capabilities=tuple(meta.get("capabilities") or ()),
                owner_plugin=owner,
            ))

        elif item.kind is ContributionKind.PROMPT:
            from clipwright.plugins.prompt_registry import PluginPromptRegistry

            # B5-T2：两种模式必须显式区分，不能把附加误作替换（PRD §7.1 第 1 条）
            mode = str(item.meta.get("mode") or "append").strip().lower()
            if mode not in ("append", "replace"):
                return f"prompt 贡献的 meta.mode 只能是 append/replace: {item.id}"

            if mode == "replace":
                from clipwright.plugins import prompt_overrides

                symbol = str(item.meta.get("symbol") or item.id)
                source = f"plugin:{owner}"
                if callable(item.object):
                    problems = prompt_overrides.register_builder_override(
                        owner, symbol, item.object, source=source,
                    )
                elif isinstance(item.object, str):
                    problems = prompt_overrides.register_constant_override(
                        owner, symbol, item.object, source=source,
                    )
                else:
                    return (f"prompt:replace {item.id} 的 object 必须是可调用对象（builder）"
                            "或字符串（常量）")
                if problems:
                    return "；".join(problems)
                return None

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
            elif kind_value == ContributionKind.ANIMATION.value:
                from clipwright.animation.registry import AnimationRegistry
                AnimationRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.STYLE_INTERPRETER.value:
                from clipwright.services.style_interpreter import StyleInterpreter
                StyleInterpreter.unregister_plugin(owner)
            elif kind_value == ContributionKind.HOOK.value:
                from clipwright.plugins.hooks import HookRegistry
                HookRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.CLIP_KIND.value:
                from clipwright.services.clip_kind_registry import ClipKindRegistry
                ClipKindRegistry.unregister_plugin(owner)
            elif kind_value == ContributionKind.PROMPT.value:
                from clipwright.plugins import prompt_overrides
                from clipwright.plugins.prompt_registry import PluginPromptRegistry

                PluginPromptRegistry.unregister(owner)
                # B5-T3：插件禁用/卸载时符号覆盖必须立刻消失，回落 prompts.py 默认值
                prompt_overrides.clear_plugin(owner)
            else:
                # 未接线的 kind 只从门面移除（本期无注册表绑定）
                pass
            recycled.extend(f"{kind_value}:{cid}" for cid in ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("贡献回收失败 %s (owner=%s): %s", kind_value, owner, e)
    return recycled

