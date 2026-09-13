"""内置贡献镜像入库（方案 A-5 的加载器零改动变体）。

背景与取舍：
- 原方案 A-5 要把 44 Tool / 4 Skill / 7 Agent / 4 Category 的**注册入口**
  换成 `Contribution(owner=BUILTIN_OWNER, ...)`。但内置注册散落在
  `tool/__init__.py`、`skill/builtin.py`、`main.py::_register_builtin_plugins`
  等 8 处，直接换入口的改动面与回归风险大；
- 本模块改为**镜像入库**：既有注册函数照常把内置落进各注册表，启动时扫描
  各注册表，把「无插件归属」的条目以 `BUILTIN_OWNER` 登记进门面
  （`dispatch=False`，避免二次注册）。门面由此获得两件事：
  1. **冲突基准**——插件注册与内置同 id 时被门面直接拒绝（G2/G3）；
  2. **管理可见**——`/api/plugin/contributions` 能列出内置与插件全量贡献。
- `dispatch=False` 是刻意的：内置已在注册表中，门面只做账本，不做转调。

调用点：`main.py` lifespan 中、所有内置注册完成之后、插件加载之前。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from clipwright.config import logger
from clipwright.plugins.contribution import (
    BUILTIN_OWNER,
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


def _collect_tools() -> list[Contribution]:
    from clipwright.tool.registry import ToolRegistry

    out: list[Contribution] = []
    for name, tool in list(getattr(ToolRegistry, "_tools", {}).items()):
        if getattr(tool, "_plugin_id", ""):
            continue  # 插件贡献，由其 owner 注册
        out.append(Contribution(ContributionKind.TOOL, name, tool))
    return out


def _collect_skills() -> list[Contribution]:
    from clipwright.skill.registry import SkillRegistry

    out: list[Contribution] = []
    for name, skill in list(getattr(SkillRegistry, "_skills", {}).items()):
        if str(getattr(skill, "_plugin_id", "") or ""):
            continue
        out.append(Contribution(ContributionKind.SKILL, name, skill))
    return out


def _collect_agents() -> list[Contribution]:
    from clipwright.agents.registry import AgentRegistry

    out: list[Contribution] = []
    for name, entry in list(getattr(AgentRegistry, "_agents", {}).items()):
        if str(getattr(entry, "plugin_id", "") or ""):
            continue
        out.append(Contribution(
            ContributionKind.AGENT, name, getattr(entry, "agent", entry),
            meta={"deps": list(getattr(entry, "deps", []) or []),
                  "description": str(getattr(entry, "description", "") or "")},
        ))
    return out


def _collect_material_sources() -> list[Contribution]:
    from clipwright.material.registry import MaterialRegistry

    out: list[Contribution] = []
    for sid, src in list(getattr(MaterialRegistry, "_sources", {}).items()):
        if str(getattr(src, "_plugin_id", "") or ""):
            continue
        out.append(Contribution(ContributionKind.MATERIAL_SOURCE, sid, src))
    return out


def _collect_categories() -> list[Contribution]:
    from clipwright.category.registry import CategoryRegistry

    owner_map: dict[str, str] = dict(getattr(CategoryRegistry, "_owner_plugin", {}) or {})
    out: list[Contribution] = []
    for cid, plugin in list(getattr(CategoryRegistry, "_plugins", {}).items()):
        if owner_map.get(cid):
            continue  # 插件注册的类型（含 user_types 动态类型）
        out.append(Contribution(ContributionKind.CATEGORY, cid, plugin))
    return out


def _collect_style_interpreter() -> list[Contribution]:
    """镜像内置风格解释器（D3 后是多注册表：只取无插件归属的槽位）。"""
    from clipwright.services.style_interpreter import StyleInterpreter

    out: list[Contribution] = []
    for owner, _priority, plugin in StyleInterpreter.list_plugins():
        if owner != StyleInterpreter._DEFAULT_SLOT:
            continue  # 插件注册的槽位由其 owner 入账
        plugin_id = str(getattr(getattr(plugin, "manifest", None), "id", "") or "builtin_style")
        out.append(Contribution(ContributionKind.STYLE_INTERPRETER, plugin_id, plugin))
    return out


def _stable_id(prefix: str, target: Any, fallback: str) -> str:
    """B4：镜像 id 用「限定名 + 内容指纹」，不用位置索引。

    位置索引（`builtin_{point}_{i}`）在注册顺序变化时会让门面残留幽灵项
    （同一 hook 换了 id，旧项还在账本里）。这里改用稳定标识：
    函数用 `module.qualname`，prompt 用内容，重复时补短指纹防碰撞。
    """
    if callable(target):
        module = str(getattr(target, "__module__", "") or "")
        qualname = str(getattr(target, "__qualname__", "") or getattr(target, "__name__", "") or "")
        ident = f"{module}.{qualname}" if qualname else fallback
    else:
        # 非可调用（prompt 文本）：只用内容——顺序变化不得改变 id
        text = str(target or "")
        ident = text[:96] if text else fallback
    fingerprint = format(id(target) & 0xFFFF, "04x")
    return f"{prefix}:{ident}#{fingerprint}"


def _collect_hooks() -> list[Contribution]:
    from clipwright.plugins.hooks import HookPoint, HookRegistry

    out: list[Contribution] = []
    for point in HookPoint:
        for i, fn in enumerate(list(getattr(HookRegistry, "_hooks", {}).get(point, []))):
            if getattr(fn, "__plugin_id__", ""):
                continue  # 插件 hook（wrapper 带归属）
            out.append(Contribution(
                ContributionKind.HOOK,
                _stable_id(f"builtin_{point.value}", fn, f"hook_{i}"),
                fn,
                meta={"point": point.value},
            ))
    return out


def _collect_prompts() -> list[Contribution]:
    from clipwright.plugins.prompt_registry import PluginPromptRegistry

    out: list[Contribution] = []
    prompts: dict[str, list[Any]] = getattr(PluginPromptRegistry, "_prompts", {}) or {}
    for agent_name, entries in list(prompts.items()):
        for i, entry in enumerate(list(entries)):
            if str(getattr(entry, "plugin_id", "") or ""):
                continue
            prompt_text = str(getattr(entry, "prompt", "") or "")
            out.append(Contribution(
                ContributionKind.PROMPT,
                _stable_id(f"builtin_{agent_name}", prompt_text, f"prompt_{i}"),
                prompt_text,
                meta={
                    "agent": agent_name,
                    "prompt": prompt_text,
                    "priority": int(getattr(entry, "priority", 0) or 0),
                    "description": str(getattr(entry, "description", "") or ""),
                },
            ))
    return out


def ingest_builtin_contributions() -> dict[str, int]:
    """扫描各注册表，把内置条目以 BUILTIN_OWNER 登记进门面（dispatch=False）。

    返回 ``{kind: count}``（供启动日志与 A-5 条目数断言测试使用）。
    幂等：重复调用只是覆盖同键（同 owner 幂等路径）。

    B6 修复：启动后新注册的内置 hook/prompt 也要能入账——本函数可被
    `/api/plugin/contributions` 等读路径按 TTL 触发重扫；同时清理本次扫描
    未再出现的镜像项（B4 的位置索引 id 会在顺序变化时留下幽灵项）。
    """
    collectors: dict[ContributionKind, Callable[[], list[Contribution]]] = {
        ContributionKind.TOOL: _collect_tools,
        ContributionKind.SKILL: _collect_skills,
        ContributionKind.AGENT: _collect_agents,
        ContributionKind.MATERIAL_SOURCE: _collect_material_sources,
        ContributionKind.CATEGORY: _collect_categories,
        ContributionKind.STYLE_INTERPRETER: _collect_style_interpreter,
        ContributionKind.HOOK: _collect_hooks,
        ContributionKind.PROMPT: _collect_prompts,
    }
    counts: dict[str, int] = {}
    for kind, collector in collectors.items():
        try:
            items = collector()
        except Exception as e:  # noqa: BLE001 — 单类失败不阻断启动
            logger.warning("内置贡献采集失败 %s: %s", kind.value, e)
            counts[kind.value] = 0
            continue
        if items:
            ContributionRegistry.register(
                BUILTIN_OWNER, items, dispatch=False,
            )
        _sweep_stale(kind, {item.id for item in items})
        counts[kind.value] = len(items)
    total = sum(counts.values())
    if total:
        logger.info(
            "内置贡献已入账门面: %d 项 (%s)",
            total, ", ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v),
        )
    return counts


def _sweep_stale(kind: ContributionKind, live_ids: set[str]) -> list[str]:
    """移除该 kind 下已不存在的内置镜像项（返回被移除的 id）。"""
    stale = [
        item.id for item in ContributionRegistry.list_by_owner(BUILTIN_OWNER)
        if item.kind is kind and item.id not in live_ids
    ]
    if not stale:
        return []
    ContributionRegistry.unregister_keys(BUILTIN_OWNER, [
        (kind, cid) for cid in stale
    ])
    logger.info("内置贡献镜像清理 %s: %d 项", kind.value, len(stale))
    return stale
