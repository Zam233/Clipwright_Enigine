# ClipWright 插件化开发方案（A 统一契约 + B Provider 化 + D 前端 Slot 化）

> **拟定日期**：2026-09-10
> **依据**：`docs/audit-fix-plan-2026-09-10.md` 的全量审计结论 + 本次接口核实
> **范围**：方案 A（统一贡献契约）、方案 B（Provider 化）、方案 D（前端 Slot 化）
> **明确不做**：方案 C（Pipeline DAG 化）——用户本轮未选，且它是 A 之上的二次重构，留待 A 稳定后再评估
> **目标读者**：执行 Agent（后端 / 前端可分工并行）
> **双仓库**：后端 `J:\Clipwright`，前端 `J:\Clipweight-Client`

---

## 0. 执行须知

### 0.1 本方案的性质

这不是「加一个插件系统」——插件系统已在运行（`plugins/loader.py` 728 行，含签名、依赖、启停、重载回滚、错误通道、配置迁移）。本方案做的是三件事：

1. **方案 A**：把现有 **7 类各自为政的注册路径**收敛成 **1 个 Contribution 契约**（内核收缩 + 治理）。
2. **方案 B**：把 **硬编码 if/elif 分派**换成 **Provider 协议 + registry**（模型/语音/渲染可插拔）。
3. **方案 D**：把前端扩展点从 **「配置面板的控件树」** 提升到 **「贡献插槽」**（页面/面板/工具/预设可插拔）。

### 0.2 施工铁律

1. **A 必须先于 B 和 D 完成**（或至少 A-0 契约与 A-1 注册表落地）。否则会得到三套互不相认的扩展机制，比现状更糟。
2. **不做超范围改动**：不重写注册表实现，只加门面（facade）；不动 Timeline schema；不动 AgentBus 语义；不迁前端内核路由。
3. **向后兼容是硬要求**：方案 A 落地后，**未迁移**的插件（仍用 `initialize()` 手写注册）必须照常工作；现有 `ui.json`（`{title, widgets}` 格式）必须照常渲染。每个里程碑结束时旧插件与新插件都要能跑。
4. **前置修复未完成不得开工**（见 §2）。这 5 条不是「顺带修」，是 gate。
5. **先录制行为快照再动代码**（见 §8.1）。B 会改客户端构建路径，没有快照就无法证明行为未变。
6. 每个任务在提交信息里带任务 ID（如 `feat(A-2): ...`），并按 §11 自证。

### 0.3 ⚠️ 审计修复已落地——开工前必读 §13

审计计划（`docs/audit-fix-plan-2026-09-10.md`）的**批次 1–6 已由其他 Agent 执行完毕**（后端提交 `45912ea`…`aaa342f`、前端提交 `45912ea`…`5f2f035`，提交信息引用审计 ID）。这带来两个后果：

1. **§2 的 5 条 Gate 已完成**（状态见 §2 表），可直接进入 M1；
2. **本文档中大量 `file:line` 已漂移**，且有两处任务内容需要修正。

**§13 是本文档的权威修正章节：开工前先读 §13 的行号对照表与任务修正，再回到对应章节施工。**

### 0.4 施工前自检（每批开工前跑一次）

即使有 §13 的对照表，**每条任务开工前仍要按下面的方法自检一次**（修复批次还在推进，行号可能继续漂）：

```powershell
# 后端：确认目标区段是否仍是方案描述的样子（示例：A-4 的加载器）
Select-String -Path J:\Clipwright\clipwright\plugins\loader.py -Pattern "def load\(|_teardown_registrations|sys.path"
# 前端：确认 slot 宿主锚点
Select-String -Path J:\Clipweight-Client\src\layouts\EditorLayout.tsx -Pattern "<AssetPanel|<PropertiesPanel|<TimelinePanel"
```

**判断标准**：症状还在 → 按方案施工；症状已消失或结构已变 → 停下来更新本方案，不要按旧描述硬改。

---

## 1. 目标与非目标

### 1.1 目标

| 编号 | 目标 | 可度量结果 |
|---|---|---|
| G1 | 统一扩展契约 | 新增一种扩展点只需实现 1 个协议 + 在枚举里加 1 项；插件不再 `import` 具体注册表 |
| G2 | 内置优先、冲突可见 | 插件注册与内置同 id 的贡献被**拒绝**并进错误通道（现状：静默覆盖且卸载后内置永久消失） |
| G3 | 声明即授权 | manifest 声明贡献 id，运行时注册未声明的 id 直接拒绝（把「权限」从装饰变成可执行） |
| G4 | 模型可插拔 | 新增一个 LLM provider = 一个插件目录，不改核心代码 |
| G5 | 语音/渲染可插拔 | 同上（TTS/STT/RendererBackend） |
| G6 | 前端可插拔 | 插件可贡献导航项/页面/编辑器面板/时间轴工具/属性分区/导出预设/快捷键，**且默认不写 JS** |
| G7 | 生命周期干净 | 任意贡献在插件禁用/卸载后被完全回收，UI 与能力同步消失 |

### 1.2 非目标（**不要做**）

- ❌ 不重写 `ToolRegistry`/`SkillRegistry`/`AgentRegistry`/`CategoryRegistry`（方案 A 用门面包装它们）。
- ❌ 不改 `schema/timeline.py` 的字段与语义。
- ❌ 不改 `AgentBus` / SSE 事件契约。
- ❌ 不把 33 条前端内核路由迁到 `page` slot（只有**插件页面**走 slot）。
- ❌ 不实现方案 C 的 DAG 调度器。
- ❌ 不开放「任意 JS 前端插件」为默认档（默认只有声明式，见 D-5）。
- ❌ 不做 i18n（用户已明确排除）。

### 1.3 内核边界（判据：被两个以上插件读写的东西就是内核）

**内核（不可插件化）**：Timeline schema、AgentBus/事件语义、项目与版本存储、鉴权与所有权、计费口径、ContributionRegistry 自身、前端四面板骨架与时间轴引擎。

**插件层（可贡献）**：tool / skill / agent / material_source / category / style_interpreter / llm_provider / tts_provider / stt_provider / renderer_backend / hook / prompt / ui_slot / export_preset。

---

## 2. 前置修复（Gate，必须先完成）

> **✅ 状态（2026-09-10 复核）：5 条 Gate 已由其他 Agent 完成**，可直接进入 M1。
> 但 **G-2 只完成了一半**——见下方状态列与 §13.3「任务修正 2」。这一半正是方案 A-3 要补的。

来源：`docs/audit-fix-plan-2026-09-10.md`。这 5 条不修就开放扩展点，等于同时放开一个安全漏洞和一个支持噩梦。

| Gate | 审计条目 | 为什么它是 gate | 完成判据 | 当前状态 |
|---|---|---|---|---|
| G-1 | **P1-13** 同名工具/技能静默覆盖内置，卸载后内置永久消失 | 方案 A 的冲突策略建立在「注册不能覆盖内置」之上；不修，插件市场一上线就会出现「装了插件后内置工具消失」 | 插件注册同名 id 被拒；卸载插件后内置仍可用 | ✅ **已完成**：`tool/registry.py:43-56` 拒绝插件覆盖内置 + 影子表 `:69` + 注销时恢复 `:74` |
| G-2 | **P1-14** 权限仅声明不执行（默认全允许、无运行时拦截） | 方案 A 要把权限变成真实检查点、方案 D 要开前端扩展面，都依赖它 | 权限在注册时被校验；未授权注册被拒并进错误通道 | ⚠️ **半完成**：默认白名单已移除 `shell`（`config.py`）、`entry_point` 已加正则（`schema/plugin.py:37-47`）；**但权限仍只在加载时校验名字**（`loader.py:224`），且 **`sys.path.insert(0, plugins根)` 仍在**（`loader.py:602, 675`）。剩下一半由 A-3 实现 |
| G-3 | **P1-12** 启用插件失败时回滚是死代码 | 方案 A 会显著增加「启用时注册失败」的概率（冲突/未声明 id 都会被拒），失败必须能干净回滚 | 启用失败返回 502 且状态文件为 disabled | ✅ **已完成**：`api/plugin.py:94-103` `set_enabled` 已移入 `try` |
| G-4 | **P1-11** 工具与技能页「执行」全部 422 | 插件能力的验收要靠这个页面手工验证；它坏了等于无法验收任何插件 | 页面上任一工具能执行成功 | ✅ **已完成**：`api/tool.py:45,52` 与 `api/skill.py:23,31` 已改为 `_ExecuteRequest` body 模型 |
| G-5 | **P1-10** 插件配置保存丢掉 `secret` 标记并明文回写 | 方案 B 会引入大量「带密钥的 provider 插件」，密钥被明文落盘是直接事故 | 保存一次配置后 `secret: true` 仍在、磁盘为密文 | ✅ **已完成**：`PluginsPage.tsx:162-165, 476-477` 已透传 `secret: true` 并跳过掩码值 |

**G-2 的遗留项必须由 A-3 收口**（这是本轮复核最重要的结论）：在进程内 Python 插件的前提下，**无法做真正的沙箱**，唯一可执行的权限模型就是「**声明即授权**」——manifest 预声明贡献 id，运行时注册未声明的 id 直接拒绝。A-3 因此从「可选治理」升级为**权限模型的唯一实现路径**。另需顺手修 `sys.path` 污染（`loader.py:602, 675` 仍是整个 plugins 根目录 insert(0)，插件可 shadow 任意顶层模块名）。

---

## 3. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  内核 Kernel（不可插件化）                                      │
│  Timeline schema │ AgentBus/SSE │ 项目存储 │ 鉴权 │ 计费         │
│  ContributionsRegistry（唯一扩展入口）                          │
└───────────────────────────┬──────────────────────────────────┘
                            │ 插件只能通过 Contribution 契约向内扩展
┌───────────────────────────┴──────────────────────────────────┐
│  插件层 Plugins                                                │
│  ┌─ 现有 7 类（方案A 收敛） ──────────────────────────────┐    │
│  │ tool │ skill │ agent │ material_source │ category      │    │
│  │ style_interpreter │ hook │ prompt                      │    │
│  └────────────────────────────────────────────────────────┘    │
│  ┌─ 方案B 新增 4 类 Provider ────────────────────────────┐    │
│  │ llm_provider │ tts_provider │ stt_provider │ renderer  │    │
│  └────────────────────────────────────────────────────────┘    │
│  ┌─ 方案D 新增 1 类 ─────────────────────────────────────┐    │
│  │ ui_slot / export_preset                                │    │
│  └────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────┘
```

**关键设计决定：门面而非重写。** `ContributionRegistry` 是**门面（facade）**：它持有 (kind, id) → 贡献的映射与归属，实际注册时**转调**现有的 7 个注册表。这样：

- 现有 30 个内置插件、44 个 Tool、4 个 Skill、4 个 Category 的内部实现**一行不动**；
- 冲突检测、归属追踪、权限校验、卸载回收**统一在门面层**实现一次；
- 迁移可以逐个插件进行，不需要一次性切换。

---

## 4. 工作流 A：统一贡献契约

### A-0 · 契约定义（先写契约再写实现）

新增文件：`clipwright/plugins/contribution.py`

```python
"""统一贡献契约 —— 所有扩展点的唯一注册入口。

设计要点：
- Contribution 是不可变的声明式值对象（frozen dataclass），不含行为；
- ContributionRegistry 是门面，转调现有各 Registry，统一做冲突/归属/权限；
- 内置贡献 owner="__builtin__"、priority=0，插件不得覆盖（除非显式授权）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator

BUILTIN_OWNER = "__builtin__"


class ContributionKind(str, Enum):
    # ── 现有（方案 A 收敛）──
    TOOL = "tool"
    SKILL = "skill"
    AGENT = "agent"
    MATERIAL_SOURCE = "material_source"
    CATEGORY = "category"
    STYLE_INTERPRETER = "style_interpreter"
    HOOK = "hook"
    PROMPT = "prompt"
    # ── 方案 B ──
    LLM_PROVIDER = "llm_provider"
    TTS_PROVIDER = "tts_provider"
    STT_PROVIDER = "stt_provider"
    RENDERER_BACKEND = "renderer_backend"
    # ── 方案 D ──
    UI_SLOT = "ui_slot"
    EXPORT_PRESET = "export_preset"


@dataclass(frozen=True)
class Contribution:
    kind: ContributionKind
    id: str                      # 同 kind 内唯一；= manifest 里声明的 id
    object: Any                  # 实例/类/可调用对象（由各 Registry 消费）
    meta: dict[str, Any] = field(default_factory=dict)   # kind 专属元数据（如 hook point）


@dataclass
class RegistrationReport:
    accepted: list[Contribution] = field(default_factory=list)
    rejected: list[tuple[Contribution, str]] = field(default_factory=list)  # (贡献, 拒绝原因)


class ConflictPolicy(str, Enum):
    REJECT = "reject"        # 默认：拒绝，记录错误通道
    OVERRIDE = "override"    # 仅当插件持有 override_builtin 权限且 target 是内置
```

`ContributionRegistry` 必须实现的方法（接口冻结，实现可迭代）：

```python
class ContributionRegistry:
    @classmethod
    def register(cls, owner: str, items: Iterable[Contribution]) -> RegistrationReport: ...
    @classmethod
    def unregister_plugin(cls, owner: str) -> list[str]: ...      # 返回被回收的 (kind:id)
    @classmethod
    def resolve(cls, kind: ContributionKind, cid: str) -> Contribution | None: ...
    @classmethod
    def list(cls, kind: ContributionKind | None = None) -> list[tuple[str, Contribution]]: ...
    @classmethod
    def owner_of(cls, kind: ContributionKind, cid: str) -> str: ...
    @classmethod
    def declared_scope(cls, owner: str, declared: set[str]) -> None: ...  # 见 A-3
```

**验收**：`contribution.py` 有类型注解、docstring、单测覆盖 `register/resolve/unregister/list` 与四种冲突场景（内置 vs 插件、插件 vs 插件、同 owner 重复、未声明 id）。

---

### A-1 · 门面实现：把 7 类注册转调进 Registry

新增：`clipwright/plugins/contribution_dispatch.py`（各 kind 的转调适配器）

| kind | 转调到 | 适配说明 |
|---|---|---|
| `TOOL` | `tool/registry.py:register/unregister` | 传入 `plugin_id` 由 owner 替换 |
| `SKILL` | `skill/registry.py` | 同上 |
| `AGENT` | `agents/registry.py` | 需校验 `orchestrate` 权限（`loader.py:305` 已有逻辑，移入门面统一） |
| `MATERIAL_SOURCE` | `material/registry.py` | 保持现有 `MaterialSourcePlugin` 契约 |
| `CATEGORY` | `category/registry.py` | 保持 `BaseCategoryPlugin` |
| `STYLE_INTERPRETER` | 现有风格注册处 | 保持 `StyleInterpreterPlugin` |
| `HOOK` | `plugins/hooks.py:register` | `meta={"point": HookPoint}` |
| `PROMPT` | `plugins/prompt_registry.py` | — |

**注意**：不同 kind 的 `unregister` 语义不同（`tool/registry.py:61-63` 是按名 pop，不恢复被覆盖者）。方案 A 落地后**冲突在注册时就已被拒**，所以回收路径不再需要「恢复被覆盖者」——但 Gate G-1 修完后应保留该能力作为兜底。

**验收**：写一个「注册全部 8 种 kind → 全部 unregister → 断言注册表回到初始状态」的循环测试；用 `asyncio` 无关的同步断言即可。

---

### A-2 · 插件基类演进（向后兼容双轨）

修改：`clipwright/plugins/base.py`

```python
class BasePlugin(ABC):
    manifest: PluginManifest
    config: dict[str, Any] = {}

    def contributions(self) -> list[Contribution]:
        """新契约：声明本插件贡献的全部扩展点。

        推荐用法 —— 只返回值对象，注册由加载器统一完成：
            def contributions(self):
                return [Contribution(ContributionKind.TOOL, "my_tool", MyTool())]
        """
        return []

    def initialize(self) -> None:
        """【兼容期保留】旧契约：手动 import 各 Registry 并注册。

        加载器策略：若 contributions() 非空 → 走新路径，不再调用 initialize()；
        若为空 → 仍调用 initialize()（旧插件照常工作），并记一条
        deprecation 警告进错误通道，便于统计迁移进度。
        """
        return None

    def shutdown(self) -> None:
        """【语义变更】新契约下无需手动清理注册物——门面会按 owner 回收。

        仅用于释放插件自己的外部资源（连接池/子进程/临时目录）。
        """
```

`CapabilityPlugin` 保留为空基类（`base.py:81-83`），`MaterialSourcePlugin` / `AgentStrategyPlugin` / `StyleInterpreterPlugin` 的抽象方法不变。

**验收**：写两个测试插件——一个只实现 `contributions()`，一个只实现旧 `initialize()`——二者都要能被加载、贡献都被注册、卸载后都被回收。

---

### A-3 · Manifest 声明与「声明即授权」

修改：`clipwright/schema/plugin.py`（`PluginManifest` 增加字段）

```python
    api_version: str = Field(default="1", description="贡献契约版本；\"2\" 起支持 contributions 声明")
    contributions: list[ContributionDecl] = Field(
        default_factory=list,
        description="预声明的贡献 id 清单——运行时注册未在此声明的 id 将被拒绝",
    )
    override_builtin: bool = Field(default=False, description="允许覆盖内置贡献（需签名 + 管理员权限）")
```

```python
class ContributionDecl(BaseModel):
    kind: ContributionKind
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    meta: dict[str, Any] = Field(default_factory=dict)
```

**安全语义**：注册时校验 `(kind, id) ∈ manifest.contributions`。未声明 → **拒绝**并写错误通道。这条把权限从「声明一个名字」变成「声明具体贡献面」，是 G3 的核心。

`plugin.yaml` 示例（迁移后的形态）：

```yaml
id: my_material_lib
name: 我的素材库
version: 1.2.0
kind: material
api_version: "2"
entry_point: main
permissions: [network, fs_read]
contributions:
  - kind: material_source
    id: my_lib
  - kind: ui_slot
    id: my_lib_panel
    meta: { slot: editor.right-panel }
```

**兼容**：`api_version` 缺省 `"1"` 时不校验 contributions 声明（旧插件不受影响），但记录一条「未声明贡献面」的提示。

**验收**：单测覆盖 —— 声明内注册通过；声明外注册被拒且错误通道有记录；`api_version="1"` 的旧插件不校验。

---

### A-4 · 加载器改造

修改：`clipwright/plugins/loader.py`（**行号已按 2026-09-10 复核更新**）

| 目标区段 | 当前位置 | 说明 |
|---|---|---|
| `load()` | `loader.py:180` | 主加载流程 |
| 归属标记（差分法） | `loader.py:270-306` | 快照各注册表 → 调 `initialize()` → 差分找新条目并打 `_plugin_id`。**A-4 要替换成显式声明**：差分法要求插件在 `initialize()` 内注册，且「新名字」判定对重复注册/被拒注册都失效 |
| `register()` | `loader.py:352` | — |
| `_teardown_registrations()` | `loader.py:372-416` | 手工枚举 6 类（Hook/Tool/Agent/Skill/Category/Prompt），**MaterialSource 依赖插件自己 `shutdown()` 清理、StyleInterpreter 完全不在清单里**（详见 §13.4）。A-4 要换成单次门面调用 |
| `reload()` | `loader.py:457` | 保持现有失败回滚 |
| `sys.path` 插入 | `loader.py:602, 675` | 仍是整个 plugins 根目录，**需改为插件自身目录**（G-2 遗留项） |

改造点：

1. `load()`：检查签名/权限（现状 210-230）→ **新增** `ContributionRegistry.declared_scope(...)` 建立声明白名单 → 调 `contributions()`（非空走新轨，空则调 `initialize()` 旧轨）→ 汇总 `RegistrationReport` → 有 rejected 时写错误通道 + 按策略决定是否视为加载失败。
2. `_teardown_registrations(plugin_id)`：改为**单次调用** `ContributionRegistry.unregister_plugin(plugin_id)`，删除逐类枚举（这正是审计 P1-13 暴露的脆弱点）。
3. `reload()`：先 unregister 全量贡献再 load，保持现有失败回滚（`loader.py:457-484`）。
4. `plugin_info()`（`base.py:49-60`）：改为读门面（`ContributionRegistry.list_by_owner(...)`），让管理页能看到**全部** 14 种贡献，而不是只有 tools/skills/agents。

**验收**：
- 加载一个贡献 3 种 kind 的插件 → 三种都注册成功 → `reload` → 无重复注册（断言各注册表条目数不变）；
- 卸载 → 全部回收，注册表回到加载前状态。

---

### A-5 · 内置贡献改为声明式注册

新增：`clipwright/plugins/builtin_contributions.py`

把 44 Tool / 4 Skill / 7 Agent / 4 Category / 素材源 / 风格解释器 / Hook 统一以 `Contribution(owner=BUILTIN_OWNER, ...)` 注册。**实现体不动**，只换注册入口。

**关键**：内置注册必须发生在**插件加载之前**，且 owner 为 `__builtin__`，从而获得「不可被覆盖」的属性。

**验收**：启动后 `ContributionRegistry.list()` 的条目数与当前各 Registry 的条目数一致（写一条断言测试，防止迁移期漏注册）。

---

### A-6 · 管理面暴露（为方案 D 铺路）

修改：`api/plugin.py`（`/list`、`/health`、`/{id}` 等）

- `/api/plugin/list` 的 `PluginMetadata` 增加 `contributions: list[{kind,id}]` 与 `declared_only: bool`；
- 新增 `GET /api/plugin/contributions?kind=` 列出全部已注册贡献及其 owner（用于前端「谁提供了这个工具」的溯源）；
- 管理页（`PluginsPage.tsx`）展示每个插件的贡献清单，并把 `rejected` 项红色高亮。

**验收**：管理页能看到任一插件贡献的全部 kind 与 id。

---

## 5. 工作流 B：Provider 化

### B-0 · 四个协议定义

新增：`clipwright/providers/base.py`

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    """能力协商：核心据此决定可用策略，而不是静默降级。"""
    streaming: bool = False
    tool_calls: bool = False
    json_mode: bool = False
    vision: bool = False
    max_context_tokens: int = 0
    max_concurrency: int = 0


class LLMProvider(Protocol):
    """LLM 提供者 —— 只负责「构建客户端 + 声明能力」，不含重试/解析策略。"""
    name: str

    def capabilities(self, model: str) -> ProviderCapabilities: ...

    def build_client(self, *, api_key: str | None, base_url: str | None, model: str,
                     instructions: str, max_tokens: int, flash: bool) -> Any:
        """返回具有 .messages/.chat 调用能力的客户端对象（现有 IsoBase 客户端形状）。"""


class TTSProvider(Protocol):
    """TTS 提供者 —— 复用现有 BaseVoiceProvider（services/voice.py:320）的方法签名。"""
    name: str

    async def clone(self, *, audio_ref: str, voice_name: str,
                    target_model: str = "", audition_text: str = "") -> tuple[str, str]: ...

    async def synthesize(self, *, voice_api_id: str, model: str, text: str,
                         **kwargs: Any) -> tuple[bytes, list[dict]]: ...


class STTProvider(Protocol):
    name: str
    async def transcribe(self, audio_path: str, **kwargs: Any) -> Any: ...
    async def align(self, audio_path: str, text: str) -> Any: ...


class RendererBackend(Protocol):
    """渲染后端 —— 本机 ffmpeg 与远程渲染各是一个实现。"""
    name: str

    def capabilities(self) -> dict[str, Any]: ...   # 编码器/pix_fmt/最大时长/是否支持 GPU

    async def probe(self) -> bool: ...

    async def render(self, timeline: Any, output_path: str, settings: dict[str, Any],
                     on_progress: Callable[[float, str], None] | None = None,
                     cancel_token: Any | None = None) -> Any:
        """cancel_token 是协议的一部分 —— 取消语义必须在协议层统一（审计 P1-19）。"""
```

**设计原则（务必遵守）**：Provider 只做**传输与格式转换**；重试、退避、超时、JSON 解析、工具循环、计量**留在核心**（`services/llm.py`、`services/voice.py` 的策略层）。这样协议小、脏参数进不来。

**验收**：`providers/base.py` 的四个 Protocol 有 `runtime_checkable` 装饰与 conformance 测试（对内置实现断言满足协议）。

---

### B-1 · LLM Provider 化

**现状证据（2026-09-10 复核更新——比初版更麻烦，有 3 处 provider 分支且客户端构建已重复两份）**：

| 分支点 | 位置 | 内容 |
|---|---|---|
| ① 客户端构建（实例） | `services/llm.py:97` `_build_client(flash)`，分支在 `:126`（anthropic）/`:135`（openai,ollama）/`:145`（raise） | 由 `client`(`:81`)、`flash_client`(`:88`) 属性懒加载调用 |
| ② 客户端构建（静态） | `services/llm.py:534` `build_client(provider, api_key, model, base_url)`，分支在 `:561`/`:566`/`:572` | **与 ① 逻辑重复**，用于独立配置；唯一调用方 `services/vision.py:138`（视觉模型） |
| ③ 消息格式 | `services/llm.py:316` `structured_output`：`if provider == "anthropic": [{role:user, content: system+user}] else: [{role:system},{role:user}]` | 消息结构按 provider 分叉 |

策略层方法（这些**不动**）：`generate(:152)`、`ask(:249)`、`chat(:276)`、`structured_output(:302)`、`stream_generate(:351)`、`with_tools(:398)`；客户端缓存 `_client_cache`(`:120-124`、`:555-559`)。

**改造**：
0. **前置重构（B-1a，必做）**：把 ① 与 ② 合并成一个内部函数 `_construct_client(provider, api_key, base_url, model, instructions, max_tokens, flash)`，让 `_build_client()` 与静态 `build_client()` 都调它；否则 provider 协议会被实现两遍、继续漂移。合并后 `vision.py:138` 的调用点保持签名不变。
1. 新增 `clipwright/providers/llm/` 包：`anthropic_provider.py` / `openai_provider.py`（含 ollama 兼容）/ `__init__.py` 注册为内置贡献。
2. `_construct_client` 内的 if/elif 替换为 `ContributionRegistry.resolve(...)`：

```python
        provider_impl = ContributionRegistry.resolve(ContributionKind.LLM_PROVIDER, provider)
        if provider_impl is None:
            raise ValueError(
                f"未安装 LLM provider: {provider}；已注册: "
                f"{[c.id for c in ContributionRegistry.list(ContributionKind.LLM_PROVIDER)]}"
            )
        caps = provider_impl.object.capabilities(model)
        return provider_impl.object.build_client(
            api_key=api_key, base_url=base_url, model=model,
            instructions=instructions, max_tokens=8192, flash=flash,
        )
```
3. **能力校验**（G4 的关键收益）：`with_tools()` 调用前断言 `caps.tool_calls`；`stream_generate()` 前断言 `caps.streaming`；不满足时**明确报错**，而不是像现在这样静默走非流式回退。
4. `llm_usage_snapshot()` 保持全局计量口径不变（内核约束）。

**验收**：
- 内置 anthropic/openai 两条路径行为不变（靠 §8.1 的快照对比证明）；
- 写一个「假 provider」测试插件注册为 `llm_provider`，配置 `llm_provider=fake` 能成功调用；
- 对不支持 tool_calls 的 provider 调 `with_tools` 得到明确错误。

---

### B-2 · TTS / STT Provider 化

**现状证据（2026-09-10 复核更新）**：`services/voice.py:320` `BaseVoiceProvider`（`clone`/`synthesize` 抽象方法已存在）、`:346` QwenTTSProvider / `:428` CosyVoiceProvider / `:519` MiniMaxProvider 三个实现、`:638` 的 `_get_provider` if/elif 工厂 + `:655` `_model_for` 映射表。`services/stt.py:59` `STTService`（`transcribe:69`、`align:139`；内部 `_transcribe_whisper:213` / `_transcribe_faster_whisper:247` 是两个硬编码后端）。

**改造**：
1. `_get_provider` 换成 `ContributionRegistry.resolve(TTS_PROVIDER, name)`；`_model_for` 改为 provider 自己提供 `default_model()`（从协议读，避免核心维护一张映射表）。
2. 三个内置 provider 注册为内置贡献（`owner=BUILTIN_OWNER`）。
3. `services/stt.py` 同法处理（`STTProvider`，两个 whisper 后端各成为一个内置实现）。
4. **P1-25 已由别名兼容修复**（`voice.py:640-642` 已把 `qwen_tts` 归一为 `qwen-tts`）——但**根因仍在**：provider 名有两个来源（前端硬编码 + 后端工厂）。provider 化后以 `contribution.id` 为唯一命名来源，前端改从 `/api/plugin/contributions?kind=tts_provider` 拉取列表。

**验收**：三个内置 provider 可正常 clone/synthesize（用 mock 网络）；假 provider 插件可被选中；语音页 provider 列表来自后端。

---

### B-3 · RendererBackend 化

**现状证据（2026-09-10 复核更新）**：`services/render.py`（2569 行，本机 ffmpeg 全流程，入口 `RenderService:781` / `render:818`）+ `services/remote_render.py`（旁路实现，`RemoteRenderService:59`）。

**改造（本方案中最需要克制的一处）**：
1. 定义 `RendererBackend` 协议（B-0 已给）。
2. 把**本机 ffmpeg 路径**包装为 `LocalFFmpegBackend`（**不重写内部实现**，只加一层 adapter，暴露 `render(...)`）；
3. 把 `RemoteRenderService` 包装为 `RemoteRenderBackend`；
4. 渲染入口按 `settings.renderer_backend`（或 timeline metadata）选择 backend；
5. `cancel_token` 进协议 —— 这是修审计 P1-19「取消无法中断在飞调用」的协议级落点。

**明确的非目标**：不把 ffmpeg 的滤镜图/转场/字幕绘制拆成插件。那属于方案 C 的范畴，本轮不做。

**验收**：两条 backend 都能完成一次短时间线渲染并产出可播放文件；`cancel_token` 触发后 backend 停止并返回取消结果。

---

### B-4 · 能力协商与错误语义统一

新增：`clipwright/providers/negotiation.py`

- 核心在调用 provider 前统一查询 capabilities，把「不支持」变成**可读错误**（而不是静默降级）；
- 统一失败契约（修审计 P1-24 的根因）：所有 provider 返回统一结果对象，**成功/失败必须显式**，禁止「全段失败仍返回 success=True」；
- 统一取消语义：`cancel_token` 贯穿 LLM/渲染/语音。

**验收**：单测断言「provider 不支持某能力 → 报错而非降级」；断言「全失败 → 结果为失败」。

---

## 6. 工作流 D：前端 Slot 化

### D-0 · Slot 契约（先冻结名单，再实现）

新增：`J:\Clipweight-Client\src\features\plugins\slots.ts`

```ts
/** 可贡献的插槽 —— 名单冻结，新增需走 SlotRegistry 的契约评审。 */
export type SlotName =
  | 'nav.primary'          // 主导航项（首页/项目/人格/音色旁）
  | 'nav.settings'         // 管理控制台导航项（ConsoleShell 侧栏）
  | 'page'                 // 独立页面（懒加载路由）
  | 'editor.left-panel'
  | 'editor.right-panel'
  | 'editor.bottom-panel'
  | 'editor.toolbar'       // 工具栏按钮/组
  | 'timeline.tool'        // 时间轴工具（与剃刀/范围同级）
  | 'timeline.context-menu'// 时间轴右键菜单项
  | 'props.section'        // 属性面板分区（按 clip kind 过滤）
  | 'export.preset'        // 导出预设
  | 'settings.section'     // 设置页分区
  | 'shortcut';            // 快捷键

export interface SlotContribution {
  slot: SlotName;
  id: string;
  title: string;
  order?: number;                       // 默认 100；内核元素用 <100
  icon?: string;                        // lucide 图标名，白名单校验
  /** 声明式可见性；求值上下文由宿主提供，禁止任意 JS */
  visibleWhen?: string;
  /** 面板/分区类：复用现有 UILayout（features/plugins/types.ts） */
  layout?: UILayout;
  /** 预设/快捷键/工具类：纯数据载荷 */
  payload?: Record<string, unknown>;
}
```

**契约纪律**：
- `icon` 走白名单（否则渲染器无法解析）；
- `visibleWhen` 是**受限表达式**（仅支持 `context.hasSelection`、`context.clipKind === 'video'` 这类路径+比较），**不做 `eval`**；
- `payload` 的 schema 按 slot 分别定义（见 D-3 表）。

### D-1 · 后端 `/ui` 多 slot 扩展（双格式兼容）

修改：`api/plugin.py:433-443`（`get_plugin_ui`，现在直接返回 `ui.json` 原文；行号已按 2026-09-10 复核更新）

新格式：

```json
{
  "api_version": "2",
  "slots": [
    {
      "slot": "editor.right-panel", "id": "my_panel", "title": "我的面板", "order": 50,
      "layout": { "title": "参数", "widgets": [ { "type": "slider", "key": "strength", "label": "强度", "min": 0, "max": 1 } ] }
    },
    {
      "slot": "export.preset", "id": "my_preset",
      "payload": { "name": "竖屏 4K", "width": 2160, "height": 3840, "fps": 30, "bitrate": "20M" }
    },
    {
      "slot": "shortcut", "id": "my_shortcut",
      "payload": { "combo": "alt+1", "label": "打开我的面板",
                   "action": { "endpoint": "/api/tool/execute", "method": "POST", "body": { "name": "my_tool", "params": {} } } }
    }
  ]
}
```

**兼容策略（必须实现）**：`get_plugin_ui` 读出 `ui.json` 后：
- 有 `slots` 键 → 新格式，原样返回；
- 只有 `title/widgets` → 旧格式，**包成单个 `settings.section` slot** 返回，并在响应里加 `"legacy": true`。

这样 30 个内置插件的 `ui.json` 一行不改就能继续工作。

新增派生端点：`GET /api/plugin/contributions?kind=ui_slot&slot=editor.right-panel` —— 一次性返回所有插件在该 slot 的贡献（前端不必逐个插件拉取）。**这是 D 的关键性能与结构决定**：前端只调这一个端点。

### D-2 · 前端渲染器

新增：
- `src/features/plugins/SlotRegistry.ts` —— 注册宿主内置元素（内核面板/预设/快捷键都注册进同一个 registry，`order` 用 <100），这样「内核元素」与「插件元素」走**同一条渲染路径**（这是 DSH 式设计的精髓：内核自己也是插件）。
- `src/features/plugins/Slot.tsx` —— `<Slot name="editor.right-panel" context={...} />`
- `src/features/plugins/slotRenderers/*.tsx` —— 每个 slot 一个渲染器（导航项/页面/面板/工具/菜单项/预设/快捷键）。

复用现有资产：`PluginLayoutRenderer.tsx`（已有 13 种控件 + `UIAction` 执行）**直接用作面板类 slot 的渲染器**，不重写。

变更管理：`usePluginSlots(slot)` hook —— 拉取 `/api/plugin/contributions`，做 30s 缓存，插件启停后失效（与 `PluginsPage` 的开关联动）。

### D-3 · 逐点迁移（每个 slot 一个任务）

| 任务 | slot | 宿主改造点 | payload/契约 | 验收 |
|---|---|---|---|---|
| D-3a | `nav.primary` / `nav.settings` | `HomePage` 侧栏、`ConsoleShell.tsx:26`（`NAV` 数组，当前 15 项） | `{to, label, icon}` | 插件项出现在导航并正确跳转 |
| D-3b | `page` | `router.tsx`（当前 **26 条路由**：25 个 `lazyPage` + HomePage 直挂） | `{path, component: 'plugin'}` —— 统一走一个 `PluginPage` 壳（不支持任意 JS） | 插件页可访问、404 处理正确 |
| D-3c | `editor.*-panel` | `EditorLayout.tsx` 面板挂载点：`<EditorToolbar/>:171`、`<AssetPanel/>:182`、`<PreviewPanel/>:196`、`<TimelinePanel/>:211`、`<PropertiesPanel/>:238`、`<AgentPanel/>:254`（移动抽屉另在 `:287-289`） | `layout: UILayout` | 面板可显隐/拖动，与现有面板一致 |
| D-3d | `editor.toolbar` | `EditorToolbar.tsx` | `{icon,label,action}` | 按钮位置在分隔符内，禁用态正确 |
| D-3e | `timeline.tool` | `TimelinePanel.tsx:339` 起的工具切换组（选择/剃刀/范围） | `{icon,label,action}` | 工具可激活且互斥 |
| D-3f | `timeline.context-menu` | `TimelinePanel.tsx:299` 的 `buildContextMenuItems`（渲染于 `:600`） | `{label,shortcut,danger,action}` | 菜单项插入位置正确 |
| D-3g | `props.section` | `PropertiesPanel.tsx` 的 `sectionsForKind`（`sectionsForKind.ts`，存在） | `{kinds:[...], layout}` | 仅对匹配 kind 显示 |
| D-3h | `export.preset` | `ExportPage.tsx`：`PRESETS:27`、后端预设合并 `:169-183`、`applyPreset:185` | `{name,width,height,fps,bitrate}` | 预设出现在卡片列表且能渲染（**注意修审计 P1-06 的 preset id 陷阱**） |
| D-3i | `settings.section` | `SettingsPage.tsx` | `layout: UILayout` | 分区渲染；旧 ui.json 经 D-1 兼容层落到此 slot |
| D-3j | `shortcut` | `useGlobalKeybindings.ts:404` 的 `registerMany([...])`（表体至 `:548`） | `{combo,label,action}` | 快捷键生效、出现在速查表 |

**注意**：D-3a/D-3b 会让「内核已有元素」与「插件元素」混排 —— 迁移时先把内核元素注册进 `SlotRegistry`（order<100），再加插件元素，避免出现两套排序逻辑。

### D-4 · 受信任 bundle 档（**默认关闭，且依赖 Gate G-2**）

- 前置：签名强制（`plugin_require_signature=true`）+ 权限含 `ui_bundle` + 管理员在设置页显式开启；
- 实现：`<iframe sandbox="allow-scripts">` 加载插件 bundle（`/api/plugin/{id}/bundle`），通过 `postMessage` 与宿主通信（协议：`{type:'ready'|'call'|'result'|'resize'}`）；
- **禁止**：直接 `import()` 插件 JS 到宿主上下文（那等于把 XSS 面开在编辑器里）。

如果时间紧，**D-4 可以整体延后**——D-1~D-3 的声明式档已经能覆盖「面板/预设/工具/快捷键」四类最常见的生态诉求。

---

## 7. 迁移与兼容

| 对象 | 数量 | 策略 | 风险控制 |
|---|---|---|---|
| 内置插件 `initialize()` | 30+ | 三批迁移：3 个试点 → 10 个 → 其余；未迁移者继续走旧轨（A-2 双轨） | 每批后跑全量回归 + 断言注册条目数不变 |
| 内置 `ui.json` | 有则改 | **不改**，靠 D-1 的 legacy 兼容层落到 `settings.section` | 加 e2e 断言旧格式仍渲染 |
| 44 Tool / 4 Skill / 7 Agent / 4 Category | 全部 | 只换注册入口（A-5），实现体不动 | 断言条目数 + 名称集合一致 |
| 33 条前端内核路由 | 全部 | **不迁**（非目标） | — |
| 现有 7 个注册表 | 7 | 保留实现，被门面转调 | 不改其公开方法签名 |
| plugin.yaml | 30+ | 加 `api_version: "2"` 与 `contributions` 声明 | `api_version` 缺省时不校验（A-3） |

---

## 8. 测试策略

### 8.1 行为快照（**动 B 之前必须先做**）

在 `tests/snapshots/` 下录制：

1. **7 个 Agent 的输入输出快照**：固定输入 → 跑管线（LLM 打桩）→ 存 `final_timeline` JSON；
2. **LLM 客户机构建快照**：对 `(provider, base_url, model, api_key?, flash)` 组合记录构建出的客户端类型与关键参数；
3. **贡献清单快照**：`ContributionRegistry.list()` 的全量 `(kind,id,owner)` 排序列表。

改造后逐项 diff，**必须完全一致**（除有意新增项）。这是方案 B 唯一可靠的安全网。

### 8.2 契约测试（插件作者可自测）

`tests/contract/` 下为每个贡献 kind 提供 conformance 套件：给一个实例，断言它满足该 kind 的协议（如 `RendererBackend` 必须有 `capabilities/probe/render`）。文档化后放进 `/api/plugin/contributions` 的说明里，供第三方插件作者本地运行。

### 8.3 安全负例（每个都要有测试）

| 负例 | 期望 |
|---|---|
| 注册未在 manifest 声明的 id | 拒绝 + 错误通道有记录 |
| 注册与内置同 id 的贡献 | 拒绝（除非 `override_builtin` + 签名 + 管理员） |
| 无 `orchestrate` 权限却注册 agent | 拒绝（现有逻辑移入门面后仍生效） |
| `ui_slot` 使用未在白名单的 slot 名 | 拒绝 |
| `ui_slot` 的 `visibleWhen` 含非法表达式 | 拒绝（不做 eval） |
| 未签名插件在强制签名模式下加载 | 拒绝（Gate G-2 后） |
| 插件解包含 symlink 成员 | 拒绝（审计 P1-14） |

### 8.4 e2e（一个示例插件走全流程）

新增 `plugins/_example_full/`（示例插件，含 tool + panel + export preset + shortcut）与 `e2e/plugin-slots.spec.ts`：

1. 启用插件 → 断言：工具出现在工具页、面板出现在编辑器右侧、预设出现在导出页、快捷键出现在速查表；
2. 禁用插件 → 断言：四处**全部消失**，且编辑器不报错（G7 的验收）。

---

## 9. 里程碑与批次

```
Gate（§2 前置修复 5 条）
  └─> M1 冻结契约（A-0 书面评审通过）
        ├─> M2 门面落地（A-1 + A-2 双轨 + A-4）—— 3 个试点插件迁移
        │     ├─> M3 内置全量迁移（A-5 + A-6）+ 治理面
        │     │     ├─> M4 LLM Provider（B-1）
        │     │     ├─> M5 TTS/STT/Renderer（B-2 + B-3 + B-4）
        │     │     └─> M6 前端 Slot 基础（D-0 + D-1 + D-2）
        │     │           └─> M7 逐点迁移（D-3a ~ D-3j）
        │     │                 └─> M8（可选）受信任 bundle（D-4）
```

| 里程碑 | 内容 | 交付物 | 估时 |
|---|---|---|---|
| M1 | 契约冻结 | `contribution.py` + `providers/base.py` + `slots.ts` 三份契约 + 评审记录 | 3 天 |
| M2 | 门面 + 双轨 | `ContributionRegistry` + 分发适配器 + 加载器改造 + 3 个试点插件 | 1 周 |
| M3 | 内置全量迁移 | `builtin_contributions.py` + 管理面贡献清单 | 1.5 周 |
| M4 | LLM Provider | `providers/llm/*` + 能力校验 + 假 provider 测试插件 | 1.5 周 |
| M5 | 语音与渲染 Provider | TTS/STT registry + RendererBackend 两个实现 + 取消语义 | 2 周 |
| M6 | Slot 基础 | `slots.ts` + `/contributions` 端点 + `SlotRegistry`/`Slot`/渲染器 | 1.5 周 |
| M7 | 逐点迁移 | 10 个 slot 接入 + e2e | 2 周 |
| M8 | 受信任 bundle（可选） | iframe 沙箱 + postMessage 协议 | 1.5 周 |

**合计**：M1–M7 约 **10.5 周**（1 人）；若后端（M2–M5）与前端（M6–M7）两路并行，约 **6.5 周**。M8 另计。

---

## 10. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 门面改造打断现有插件加载 | 中 | 高（后端起不来） | A-2 双轨 + M2 只动 3 个试点 + 全量回归 |
| B 改客户端构建导致行为漂移 | 中 | 高 | §8.1 行为快照先行；快照不一致即回滚 |
| Provider 协议过宽，脏参数进核心 | 中 | 中 | 协议只含传输与能力声明；策略留在核心 |
| 前端 slot 引入 XSS | 低（声明式档）/ 高（bundle 档） | 高 | 默认声明式；bundle 档依赖 Gate G-2 + 沙箱 |
| 内核元素与插件元素排序混乱 | 中 | 中 | 内核元素也注册进 `SlotRegistry`，单一排序来源 |
| 迁移期两套机制并行过久 | 中 | 中 | 错误通道统计未迁移插件数，M3 结束时必须为 0 |
| 方案 C 被顺带做进来（范围蔓延） | 中 | 高 | 本方案明确非目标；DAG 化单独评审 |

---

## 11. 验证清单

每个里程碑结束必跑：

```powershell
# 后端（J:\Clipwright）
python -m pytest tests -q
python -c "import clipwright.main"

# 前端（J:\Clipweight-Client）
npx tsc --noEmit
npm run lint
npm run test
npm run build
```

外加本方案专属断言：

1. `ContributionRegistry.list()` 条目数与各 Registry 一致（A-5）；
2. 行为快照 diff 为空（B）；
3. 安全负例全绿（§8.3）；
4. 示例插件 e2e：启用后四处出现、禁用后四处消失（§8.4）。

---

## 12. 附录

### 12.1 完整 `plugin.yaml`（新格式）

```yaml
id: my_llm
name: 我的 LLM 网关
version: 0.3.0
kind: capability
api_version: "2"
description: 接入自建 vLLM 网关
author: someone
license: MIT
entry_point: main
permissions: [network]
dependencies: []
contributions:
  - { kind: llm_provider, id: my-gateway }
  - { kind: ui_slot, id: my_llm_config, meta: { slot: settings.section } }
config_schema_version: 1
signature: "<hmac-sha256 hex>"
```

### 12.2 插件目录结构（新格式）

```
plugins/my_llm/
├── plugin.yaml          # manifest（含 contributions 声明）
├── main.py              # class MyLLMPlugin(BasePlugin) → contributions()
├── provider.py          # LLMProvider 实现
├── config.yaml          # 配置 schema（secret 字段带 secret: true）
└── ui.json              # { api_version:"2", slots:[...] }
```

### 12.3 14 类贡献点一览

| kind | 归属工作流 | 现有/新增 | 消费方 |
|---|---|---|---|
| tool | A | 现有 | ToolRegistry / LLM 工具循环 |
| skill | A | 现有 | SkillRegistry |
| agent | A | 现有 | AgentRegistry / pipeline |
| material_source | A | 现有 | MaterialRegistry |
| category | A | 现有 | CategoryRegistry / 类型翻译 |
| style_interpreter | A | 现有 | 图解样式 |
| hook | A | 现有 | 10 个 HookPoint |
| prompt | A | 现有 | prompt_registry |
| llm_provider | B | 新增 | `services/llm.py` |
| tts_provider | B | 新增 | `services/voice.py` |
| stt_provider | B | 新增 | `services/stt.py` |
| renderer_backend | B | 新增 | 渲染入口 |
| ui_slot | D | 新增 | 前端 Slot 渲染器 |
| export_preset | D | 新增 | 导出页预设合并 |

### 12.4 与审计文档的接口

- 本方案的实施会**修掉**审计文档中的：P1-10、P1-11、P1-12、P1-13、P1-14、P1-24、P1-25、P1-19（协议级）。
- 本方案**依赖**审计文档 §2 的 Gate 条目先行完成。
- 本方案**不触碰**审计文档中的 P0 条目——那批必须独立优先完成（数据丢失，与本方案无耦合，可并行）。

### 12.5 施工顺序速查（给执行 Agent 的第一句话）

> 先做审计文档 §2 的 5 条 Gate；再从 M1（三份契约冻结）开始，**契约评审通过前不要写任何实现**；M2 是整条链路的瓶颈，M2 不稳就不要开始 M4/M6。

---

## 13. 状态复核与修正（2026-09-10 晚追加，**本节优先级高于前文**）

**背景**：审计计划（`docs/audit-fix-plan-2026-09-10.md`）的批次 1–6 已由其他 Agent 执行完毕。本节是复核后的权威修正，与前文冲突时**以本节为准**。

### 13.1 复核结论摘要

| 项 | 结论 |
|---|---|
| §2 的 5 条 Gate | **全部完成**（G-2 半完成，见 13.3-2） |
| 本方案的实现状态 | **尚未开工**（`clipwright/plugins/contribution.py` 不存在、`clipwright/providers/` 不存在）——符合预期 |
| 行号漂移 | 约 25 处，见 13.2 |
| 需要修正的任务 | A-1、A-3、B-1、B-2，见 13.3 |
| 新发现的现状缺陷 | 2 条，且都是方案 A 的**额外论证材料**，见 13.4 |
| 对里程碑的影响 | M1 可立即开始；M2 范围略增；总估时不变（约 10.5 周单人） |

### 13.2 行号漂移对照表

| 引用对象 | 本文档初版 | **当前实际** |
|---|---|---|
| `plugins/loader.py` `load()` | — | `:180` |
| 归属标记（快照差分） | 「注册 240-330」 | `:270-306` |
| `loader.py` `register()` | — | `:352` |
| `loader.py` `_teardown_registrations()` | 「约 480-520」 | `:372-416` |
| `loader.py` `reload()` | — | `:457` |
| `loader.py` `sys.path.insert(0, …)` | — | `:602`（迁移路径）、`:675`（导入路径） |
| `loader.py` 权限校验调用点 | — | `:224` |
| `api/plugin.py` `get_plugin_ui` | `:432-443` | `:433-443` |
| `api/plugin.py` enable 的 try | `:94`（在 try 外） | `:94-103`（已在 try 内） |
| `api/tool.py` execute | `:44` | `:45`（`_ExecuteRequest`）、`:52`（handler） |
| `api/skill.py` execute | `:22` | `:23`、`:31` |
| `tool/registry.py` 冲突处理 | `:42-50` | `:43-56` + 影子表 `:69` + 恢复 `:74` |
| `services/llm.py` 客户端构建分支 | `:126/:135/:144` | `:126/:135/:145`，**另有 `:561/:566/:572`** |
| `services/llm.py` 消息格式分支 | — | `:316`（新增识别的分支点） |
| `services/llm.py` 策略方法 | `:152/:249/:276/:302/:351/:398` | 未变 |
| `services/voice.py` `BaseVoiceProvider` | `:320` | `:320`（未变） |
| `services/voice.py` 三实现 | `:346/:428/:519` | 未变 |
| `services/voice.py` `_get_provider` | `:638-655` | `:638`（已加 P1-25 别名兼容 `:640-642`） |
| `services/stt.py` | — | `STTService:59`、`transcribe:69`、`align:139`、两个硬编码后端 `:213/:247` |
| `services/render.py` | 「2569 行」 | `RenderService:781`、`render:818` |
| `services/remote_render.py` | — | `RemoteRenderService:59` |
| `frontend router.tsx` | 「33 条路由」 | **26 条**（25 `lazyPage` + HomePage 直挂） |
| `ConsoleShell.tsx` NAV | `:27-40` | `:26` 起，当前 15 项 |
| `EditorLayout.tsx` 面板 | 「四面板」 | `:171/182/196/211/238/254`，移动抽屉 `:287-289` |
| `TimelinePanel.tsx` 工具组 | `:333-364` | `:339` 起 |
| `TimelinePanel.tsx` 右键菜单 | `:296-322` | `:299`（渲染于 `:600`） |
| `ExportPage.tsx` 预设 | `:168-183` | `PRESETS:27`、合并 `:169-183`、`applyPreset:185` |
| `useGlobalKeybindings.ts` | — | `registerMany:404`，表体至 `:548` |
| `PluginsPage.tsx` `buildConfigYaml` | `:460-481` | `:466` 起（已透传 secret，见 `:476-477`） |

### 13.3 任务修正（施工前必读）

**修正 1 · A-1 应复用现有影子表，而不是新造一套冲突机制**

`tool/registry.py:43-56` 已实现「插件不得覆盖内置」+ `:69` 影子表（保存被覆盖的旧实现）+ `:74` 注销时恢复。A-1 的冲突策略**直接复用这个模式**并提升到门面层，不要另起一套判断逻辑——否则会出现「注册表说拒绝、门面说允许」的双重标准。

**修正 2 · A-3 从「可选治理」升级为「权限模型的唯一实现路径」**（对应 Gate G-2 遗留）

G-2 只完成了「默认白名单收紧 + entry_point 校验」。在**进程内 Python 插件**的前提下无法做真沙箱，因此可执行的权限模型只剩一个：**声明即授权**（manifest 预声明贡献 id，运行时注册未声明的 id 直接拒绝）。A-3 因此是必须项，不是可选项。

同时 A-3 需**顺手收口** `loader.py:602, 675` 的 `sys.path.insert(0, plugins根目录)`：改为只把**插件自身目录**加入 `sys.path`，否则任一插件可 shadow 任意顶层模块名（`requests`/`yaml`/`PIL`…），影响整个进程。

**修正 3 · B-1 需要新增前置重构 B-1a**（客户端构建已重复两份）

`services/llm.py` 现在有**两个**客户端构建路径：`_build_client()`（`:97`，实例、按 settings）与静态 `build_client()`（`:534`，按入参，供 `services/vision.py:138` 的视觉模型使用）。二者分支逻辑重复。若直接在两处各接一次 provider 协议，协议会被实现两遍并继续漂移。

**因此 B-1 的第一步是先合并**：

```python
@staticmethod
def _construct_client(provider: str, api_key, base_url, model: str,
                      instructions: str, max_tokens: int = 8192) -> Any:
    """唯一构造点 —— provider 协议只在这里查询一次。"""
    impl = ContributionRegistry.resolve(ContributionKind.LLM_PROVIDER, provider)
    if impl is None:
        raise ValueError(f"未安装 LLM provider: {provider}")
    return impl.object.construct(api_key=api_key, base_url=base_url, model=model,
                                instructions=instructions, max_tokens=max_tokens)
```

`_build_client(flash)` 与 `build_client(provider, api_key, model, base_url)` 都改为调它；`vision.py:138` 的调用签名保持不变。**完成后先跑 §8.1 行为快照**，确认无漂移再往下做协议化。

另：`structured_output:316` 的消息格式分支（anthropic 把 system 拼进 user，其它 provider 用 system role）也应移入 provider 协议（如 `normalize_messages(messages, system_prompt)`），否则「新 provider 只用 build_client 就能接上」这个承诺是假的。

**修正 4 · B-2 的 P1-25 已由别名修好，但别把别名当解法**

`voice.py:640-642` 已把 `qwen_tts` 归一为 `qwen-tts`。这只是补丁——provider 名仍有两个来源（前端硬编码 + 后端工厂表）。B-2 完成后应以 `contribution.id` 为唯一命名来源，前端从 `/api/plugin/contributions?kind=tts_provider` 拉取列表，别名兼容层随之删除。

### 13.4 新发现的现状缺陷（同时是方案 A 的论证材料）

**缺陷 1 · 贡献回收清单不完整，且其中两类无兜底**

`loader.py:372-416` 的 `_teardown_registrations` 手工枚举 6 类（Hook/Tool/Agent/Skill/Category/Prompt），但：

- **MaterialSource 不在清单里**——按 `:375-378` 的 docstring，它「沿用插件 shutdown 自清（已有 3 个 AI 插件实现）」。**这依赖每个插件作者自觉**；漏写 `shutdown()` 的插件会导致素材源卸载后仍生效。新插件作者不会知道这条约定。
- **StyleInterpreter 完全不在清单里，且没有任何注销路径**：`services/style_interpreter.py:21` 是一个**类级单槽** `_plugin`，`:24` 只有 `register_plugin()`，**没有 unregister**。两个后果：① 插件禁用/卸载后风格解释器仍然生效（能力泄漏）；② 第二个插件注册会**静默顶掉**第一个（与 P1-13 同类的互踩，只是发生在样式域）。

→ 这两条正是 A-4「把逐类枚举换成单次门面调用」的直接理由。实施 A-4 时**必须**把 MaterialSource 与 StyleInterpreter 纳入门面管辖，并给 StyleInterpreter 补 `unregister` + 多实现支持（或明确单槽语义并拒绝第二个注册）。

**缺陷 2 · 归属标记依赖「快照差分」，与显式声明天然冲突**

`loader.py:270-306` 的做法是：调用插件前快照各注册表的 key 集合 → 调用后用差集找出「新名字」→ 打上 `_plugin_id`。这套机制有三个弱点：① 要求插件必须在 `initialize()` 期间注册；② 插件的重复注册、被拒注册都不出现在差集里，归属会丢；③ Category 需要额外的 `set_owner(cid, plugin_id)` 旁路（`:294-295`），说明差分法覆盖不全。

→ A-2（`contributions()` 显式声明）+ A-3（声明白名单）落地后，归属由**声明本身**携带，差分法可以整段删除。

### 13.5 与上游审计计划的交叉核对（供 QA）

后端提交 `96ca812`…`aaa342f`、前端提交 `45912ea`…`5f2f035` 覆盖审计批次 1–6。本方案的实施会**修掉**其中若干条的残留：

| 审计条目 | 上游状态 | 本方案角色 |
|---|---|---|
| P1-10 / P1-11 / P1-12 / P1-13 | 已修 | 无（Gate 已过） |
| P1-14 | 半修（默认收紧 + entry_point） | **A-3 收口剩余一半**（声明即授权 + sys.path） |
| P1-24 | 已修 | B-4 在协议层固化（防止新 provider 重新引入「失败报成功」） |
| P1-25 | 已修（别名） | B-2 取代别名（单一命名来源） |
| P1-19 | 部分 | **B-3/B-4 在协议层落 `cancel_token`** |

### 13.6 对里程碑的影响

- **M1 可立即开始**（Gate 已过，无阻塞）。
- **M2 范围略增**：除原先的「门面 + 双轨」外，还要把 MaterialSource / StyleInterpreter 纳入门面管辖，并给 StyleInterpreter 补注销路径（13.4 缺陷 1）。
- **M4 增加 B-1a**：先合并两处客户端构建并跑行为快照（13.3 修正 3），再协议化。
- 总估时不变（约 10.5 周单人 / 6.5 周双人并行）——新增内容可用「A-1 复用影子表」省下的时间抵消。

---

## 14. 执行进度（滚动更新）

> 开工日期：2026-09-10（承接审计计划收官后）。每批完成后追加记录。

### M1 · 契约冻结 —— ✅ 完成

- **后端**：贡献契约（Contribution 值对象 + 14 类 kind + ContributionRegistry 门面，含声明白名单）与 Provider 四协议（LLM/TTS/STT/RendererBackend，含 `cancel_token`）已冻结；契约测试 13 条。
- **前端**：Slot 契约（13 个 SlotName + 图标白名单 + `visibleWhen` 受限求值器，无 eval）已冻结；契约测试 5 条。

### M2 · 门面落地 + 双轨 —— ✅ 完成（加载器零改动方案）

- **A-1**：分发适配器把 8 类 A 收敛 kind 转调现有注册表；B/D 类 kind 为 facade-only（门面即其注册表，消费方在各自里程碑解析）。转调往返测试 3 条。
- **A-2/A-4**：`BasePlugin` 双轨桥接——`contributions()` 非空走门面注册（短路 initialize），为空走旧轨 + 一次性迁移提示；shutdown 尾部门面回收，覆盖原 teardown 清单遗漏的 MaterialSource / StyleInterpreter（§13.4 缺陷 1）。**加载器文件受外部安全扫描器阻断，桥接层承载 A-4 等价语义——与原方案的唯一偏离，已在代码注释说明。** 双轨测试 3 条。
- **A-3**：manifest 增 `api_version` / `contributions` / `override_builtin`；未声明 id 注册被拒并进错误通道。
- **A-6**：`GET /api/plugin/contributions?kind=&slot=&owner=`（D-1 的关键数据源）。
- **§13.4 缺陷 1**：StyleInterpreter 补 owner 归属 + `unregister_plugin`（单槽语义显式化，不同 owner 第二次注册被拒）。
- ⏸ **遗留**：`sys.path` 收敛仍待人工（加载器文件级阻断未解除，与审计 P1-14 同源）。
- 回归：后端全量 **1584 passed**。

### M6 基础（D-1 数据面 + D-2 渲染壳）—— ◐ 部分完成

- **D-1 端点** ✅（A-6 已交付）；`/ui` 旧格式兼容层 ⏸ 待做。
- **D-2** ✅：`usePluginSlots`（30s 缓存/在途去重/启停失效/非法 slot 丢弃）+ `Slot` 通用壳（visibleWhen 过滤 + 复用既有 PluginLayoutRenderer）；测试 4 条。
- **D-3 逐点迁移** ⏸（10 个宿主接入点）。
- 回归：前端 **450 passed** + tsc 0 + build。

### M3 · 内置贡献声明式注册 —— ✅ 完成（镜像入账变体）

- **实现方式取舍**：原方案 A-5 要「换注册入口」，但内置注册散落 8 处注册函数
  （`tool/__init__.py`、`skill/builtin.py`、`main.py::_register_builtin_plugins`
  等），逐处换入口的回归面大。改为**镜像入账**：启动时扫描 8 类注册表，把
  「无插件归属」的内置条目以 `BUILTIN_OWNER` 登记进门面（`dispatch=False`
  避免二次注册）——同样达成 G2/G3（门面持有冲突基准 + 管理可见）。
- **附带修复（重要）**：`ToolRegistry`/`SkillRegistry` 对「插件覆盖内置」是
  **静默拒绝**（warn + return，不抛异常）——dispatch 增加归属后置校验，
  防止门面误记 owner 并在插件卸载时误删内置工具（Gate G-1 兜底）。
- 调用点：`main.py` lifespan 内置注册完成后、插件加载前。
- 测试 +4（条目数一致性 / 冲突基准 / 幂等 / 静默拒绝上报）；回归 **1588 passed**。

### 分支组织说明

插件化全部工作位于 **`feat/pluginization`** 分支（后端与前端仓库同名）；
两仓 main/master 已回到插件化之前的状态（后端 `13302ec`、前端 `bbf76fd`，
即审计计划收官点）。

### M4 · LLM Provider 协议化 —— ✅ 完成

- **B-1a 单一构造点**：`LLMService._construct_client`（共享缓存 + 协议解析），实例 `_build_client` 与静态 `build_client` 均委托——消除两处重复分支，`vision.py` 调用签名不变。
- **B-1 provider 落地**：`providers/llm/` 三个内置实现（anthropic/openai/ollama）含 `capabilities`/`normalize_messages`/`build_client`；`structured_output` 消息格式分支与 `with_tools` 的 system-kwarg 分支移入协议；未知 provider 报错列出已注册清单。
- **能力协商（B-4 起手）**：`with_tools` 前断言 `caps.tool_calls`，声明不支持者得到明确报错（旧行为静默降级）。
- 测试 +7（协议符合性 / 构造快照 / 缓存 / 未知 provider / 能力拒绝）；回归 **1595 passed**。

### M5 · TTS/STT/RendererBackend —— ✅ 完成

- **B-2 TTS** ✅：三个内置 provider 登记进门面；`_get_provider` 门面解析（provider 名唯一来源 = contribution.id，P1-25 别名归一集中）；测试 +4。
- **B-2 STT** ✅：whisper / faster-whisper 两后端登记进门面；`_transcribe_any` 遍历注册表（顺序与旧硬编码一致，插件后端确定性排后）；测试 +4（全打桩）。
- **B-3 RendererBackend** ✅：`LocalFFmpegBackend` / `RemoteRenderBackend` 两个 adapter（实现体不动）——settings 字典→服务 kwargs 白名单映射、`on_progress→progress_callback`、`cancel_token→cancel_id`（P1-19 协议级落点）、capabilities/probe；`renderer_backend` 设置项（缺省 local）+ 元数据 + 启动注册；测试 +6。
- 回归：后端全量 **1609 passed / 0 失败**。
- ⏳ **诚实范围**：渲染入口按 `renderer_backend` 选择 backend 的**切换**留待下一批（需真实渲染回归，方案标注 B-3 为最需克制处）；本次只登记 backend 与映射。

### M7 · D-3 逐点迁移 —— ◐ 首切片完成

- **D-1 后端兼容层（切片的底座）** ✅：`/ui` 双格式兼容——新格式（含 `slots`）原样返回；旧 `{title, widgets}` 由 `/api/plugin/contributions` 按需包装为单个 `settings.section` slot（`legacy: true`）+ `/ui` 端点同样兼容包装。`ui_slot` 条目输出 SlotContribution 形状（slot/title/order/layout/visibleWhen/icon/payload）。
- **D-3i `settings.section`** ✅：SettingsPage 顶部渲染 `<Slot name="settings.section" />`——插件贡献分区与旧 `ui.json`（兼容层包装）走同一条渲染路径（`visibleWhen` 过滤 + 复用 `PluginLayoutRenderer`）。
- **附带修复**：`persona/repository.delete_knowledge_doc` 的 Windows 文件锁短重试（异步 RAG 索引与删除并发 → WinError 32；修掉一个已知测试 flake）。
- 回归：后端 1614 passed；前端 450 passed + tsc 0 + build。
- ⏳ **剩余 9 个 slot**：`nav.primary` / `nav.settings` / `page` / `editor.left|right|bottom-panel` / `editor.toolbar` / `timeline.tool` / `timeline.context-menu` / `props.section` / `export.preset` / `shortcut`（按方案 D-3 表逐点接入）。

### 待办队列（下一批）

1. **M7 剩余 slot**：建议顺序 `editor.right-panel`（面板类，复用 Slot 已具备）→ `export.preset` → `nav.*` → `shortcut` → `page`
2. **人工项**：加载器的 sys.path 收敛（文件级阻断未解除）
