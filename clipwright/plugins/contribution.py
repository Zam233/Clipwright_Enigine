"""统一贡献契约 —— 所有扩展点的唯一注册入口（方案 A / M1 契约冻结）。

设计要点（依据 `docs/pluginization-plan-2026-09-10.md` §4 A-0 与 §13.3）：
- Contribution 是不可变的声明式值对象（frozen dataclass），不含行为；
- ContributionRegistry 是**门面（facade）**，不重写任何现有注册表——实际
  注册经 `contribution_dispatch` 转调现有 7 个 Registry（A-1 落地；未安装
  dispatch 模块时退化为契约-only 模式，便于 M1 先行与单测隔离）；
- 内置贡献 owner="__builtin__"、priority 隐含最高，插件不得覆盖（除非显式
  声明 override_builtin 且持有签名——由加载器在入口校验）；
- 「声明即授权」（A-3）：插件在 manifest 预声明 (kind, id)，运行时注册未声明
  的 id 直接拒绝——这是进程内 Python 插件唯一可执行的权限模型（Gate G-2 遗留）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional

BUILTIN_OWNER = "__builtin__"


class ContributionKind(str, Enum):
    """14 类可贡献扩展点（名单冻结；新增需走契约评审）。"""
    # ── 现有（方案 A 收敛）──
    TOOL = "tool"
    SKILL = "skill"
    AGENT = "agent"
    MATERIAL_SOURCE = "material_source"
    CATEGORY = "category"
    STYLE_INTERPRETER = "style_interpreter"
    HOOK = "hook"
    PROMPT = "prompt"
    # ── 方案 B：Provider 化 ──
    LLM_PROVIDER = "llm_provider"
    TTS_PROVIDER = "tts_provider"
    STT_PROVIDER = "stt_provider"
    RENDERER_BACKEND = "renderer_backend"
    # ── 方案 D：前端 Slot 化 ──
    UI_SLOT = "ui_slot"
    EXPORT_PRESET = "export_preset"


class ConflictPolicy(str, Enum):
    """冲突策略（A-1 复用 tool/registry 影子表模式，不下沉到门面重造）。"""
    REJECT = "reject"        # 默认：拒绝，记录错误通道
    OVERRIDE = "override"    # 仅当插件持有 override_builtin 且目标是内置


@dataclass(frozen=True)
class Contribution:
    """一个扩展点的声明式值对象。

    - ``id`` 同 kind 内唯一，必须与 manifest ``contributions`` 声明的 id 一致；
    - ``object`` 是实例/类/可调用对象，由各 kind 的消费方定义契约；
    - ``meta`` 承载 kind 专属元数据（如 hook 的 point、ui_slot 的 slot 名）。
    """
    kind: ContributionKind
    id: str
    object: Any
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """全局键：``kind:id``（声明白名单与清单快照共用同一形态）。"""
        return f"{self.kind.value}:{self.id}"


@dataclass
class RegistrationReport:
    """一次批量注册的结果。

    ``rejected`` 保留 (贡献, 拒绝原因) 二元组，供加载器写错误通道并决定
    是否将本次加载视为失败（策略见 A-4）。
    """
    accepted: list[Contribution] = field(default_factory=list)
    rejected: list[tuple[Contribution, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


def _dispatch_register(owner: str, item: Contribution) -> Optional[str]:
    """转调到各注册表（A-1 的 `contribution_dispatch`）。

    返回 None 表示成功，返回字符串表示拒绝原因。
    dispatch 模块未安装（M1 契约-only 模式）时视为成功——注册表状态只由本
    门面维护，供契约测试与灰度期使用。
    """
    try:
        from clipwright.plugins import contribution_dispatch as _dispatch
    except ImportError:
        return None
    return _dispatch.register(owner, item)


def _dispatch_unregister(owner: str, keys: list[str]) -> list[str]:
    """回收指定 owner 的全部贡献；返回实际回收的 ``kind:id`` 列表。"""
    try:
        from clipwright.plugins import contribution_dispatch as _dispatch
    except ImportError:
        return list(keys)
    return _dispatch.unregister(owner, keys)


class ContributionRegistry:
    """统一贡献门面 —— (kind, id) → Contribution 的映射 + 归属 + 声明白名单。

    线程安全性：注册发生在插件加载/启停路径（单线程事件循环 + 少量同步
    入口），与现有各 Registry 一致，不加锁（不改其并发模型）。
    """

    _items: dict[tuple[ContributionKind, str], Contribution] = {}
    _owners: dict[tuple[ContributionKind, str], str] = {}
    _declared: dict[str, set[str]] = {}

    # ── 声明白名单（A-3）──────────────────────────────

    @classmethod
    def declared_scope(cls, owner: str, declared: Iterable[str]) -> None:
        """登记某 owner 预声明的贡献面（``{"kind:id", ...}``）。

        加载器在调用插件前调用本方法；之后该 owner 注册未声明的 id 一律
        被拒。「声明即授权」的唯一执行点。
        """
        cls._declared[str(owner)] = {str(x) for x in declared}

    @classmethod
    def declared_keys(cls, owner: str) -> set[str]:
        return set(cls._declared.get(str(owner), set()))

    @classmethod
    def clear_declared(cls, owner: str) -> None:
        cls._declared.pop(str(owner), None)

    # ── 注册 / 回收 ───────────────────────────────────

    @classmethod
    def register(cls, owner: str, items: Iterable[Contribution],
                 policy: ConflictPolicy = ConflictPolicy.REJECT) -> RegistrationReport:
        """批量注册贡献，逐条执行五项校验（顺序即优先级）。

        校验顺序：
        1. owner 为插件时：必须已声明白名单，且 ``item.key`` 在白名单内
           （未声明 → 拒绝；这是 A-3 的核心）；
        2. 与内置冲突：拒绝（策略非 OVERRIDE 时）；
        3. 与其它插件冲突：拒绝；
        4. 同 owner 重复注册：允许（幂等覆盖——reload/热重载的正常路径）；
        5. 通过 → 转调 dispatch 落进各注册表。
        """
        owner = str(owner or "")
        report = RegistrationReport()
        for item in items:
            reason = cls._validate(owner, item, policy)
            if reason is None:
                reason = _dispatch_register(owner, item)
            if reason is not None:
                report.rejected.append((item, reason))
                continue
            key = (item.kind, item.id)
            cls._items[key] = item
            cls._owners[key] = owner
            report.accepted.append(item)
        return report

    @classmethod
    def _validate(cls, owner: str, item: Contribution,
                  policy: ConflictPolicy) -> Optional[str]:
        if not item.id:
            return "贡献 id 不能为空"
        key = (item.kind, item.id)
        existing_owner = cls._owners.get(key)
        declared = cls._declared.get(owner)
        # 声明即授权（内置豁免：BUILTIN_OWNER 不经 manifest 加载）
        if owner != BUILTIN_OWNER and declared is not None and item.key not in declared:
            return f"贡献未在 manifest 声明: {item.key}"
        if existing_owner is not None:
            if existing_owner == owner:
                return None  # 同 owner 幂等覆盖
            if existing_owner == BUILTIN_OWNER:
                if policy is ConflictPolicy.OVERRIDE:
                    return None  # 显式授权覆盖内置（签名 + 管理员由入口校验）
                return f"不得覆盖内置贡献: {item.key}"
            return f"贡献已被 {existing_owner} 注册: {item.key}"
        return None

    @classmethod
    def unregister_plugin(cls, owner: str) -> list[str]:
        """回收某 owner 的全部贡献；返回被回收的 ``kind:id`` 列表。"""
        owner = str(owner or "")
        keys = [k for k, o in cls._owners.items() if o == owner]
        if not keys:
            return []
        names = [f"{k[0].value}:{k[1]}" for k in keys]
        _dispatch_unregister(owner, names)
        for k in keys:
            cls._items.pop(k, None)
            cls._owners.pop(k, None)
        cls.clear_declared(owner)
        return names

    # ── 查询 ─────────────────────────────────────────

    @classmethod
    def resolve(cls, kind: ContributionKind, cid: str) -> Optional[Contribution]:
        return cls._items.get((kind, str(cid)))

    @classmethod
    def list(cls, kind: Optional[ContributionKind] = None) -> list[tuple[str, Contribution]]:
        """列出贡献：``[(owner, contribution), ...]``，按键排序保证确定性。"""
        out: list[tuple[str, Contribution]] = []
        for key, item in cls._items.items():
            if kind is not None and key[0] is not kind:
                continue
            out.append((cls._owners.get(key, ""), item))
        out.sort(key=lambda pair: (pair[1].kind.value, pair[1].id))
        return out

    @classmethod
    def list_by_owner(cls, owner: str) -> list[Contribution]:
        owner = str(owner or "")
        return [c for c in cls._items.values() if cls._owners.get((c.kind, c.id)) == owner]

    @classmethod
    def owner_of(cls, kind: ContributionKind, cid: str) -> str:
        return cls._owners.get((kind, str(cid)), "")

    # ── 测试/重置 ─────────────────────────────────────

    @classmethod
    def clear_all(cls) -> None:
        """清空门面状态（测试用；不影响各注册表本体）。"""
        cls._items.clear()
        cls._owners.clear()
        cls._declared.clear()
