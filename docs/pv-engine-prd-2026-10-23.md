# pv_engine 插件二期 PRD —— 能力补齐、契约修复与多 Agent 接线

- 版本：v1.0（2026-10-23）
- 范围：`plugins/pv_engine`（现有插件）× `K:\video engine`（逐帧渲染引擎）
- 上游文档：[pv-engine-plugin.md](pv-engine-plugin.md)（v1 接入记录）、[mv-pv-prd-2026-10-18.md](mv-pv-prd-2026-10-18.md)（C1–C13 能力清单，本文件不重复其 Part I）
- 结论先行：**插件当前只把引擎的三条旁路能力（体检/审计/抽帧）接了出来，引擎真正的产品价值（脚手架、整片渲染、拍点、逐字对齐、排版语法）没有接；且三条工具里没有任何一条能被阶段 Agent 看见。**

---

## 0. 一页结论

| 问题 | 事实 | 证据 |
|---|---|---|
| 插件暴露了多少 | 3 个 Tool + 1 个 Skill | `plugins/pv_engine/main.py:235-241` |
| 引擎有多少条 CLI 入口 | 6 条（init/info/check/stills/render/beats） | `K:\video engine\engine\cli.py:141-182` |
| 插件覆盖了几条 | 2 条（check、stills），且都要求**旧仓库布局** `_build/` | `plugins/pv_engine/adapter.py:27-28,125-141` |
| 主 Agent 能看见吗 | 能看见 4 项（3 工具 + 1 技能），未配置时调用报错 | 实测 `build_surface()` → `pv_engine_audit/probe/review/stills` |
| 阶段 Agent 能看见吗 | **0 项**（`is_configured()` 恒 False） | 实测 `ToolRegistry.list_agent_callable()` → `[]` |
| 其余 5 个 Agent 能看见吗 | **根本没有 LLM tool-use 循环**，谈不上看见 | `agents/*.py` 只有 `structure_agent.py:522` 构造 `tools=` |
| 插件配置页有用吗 | 无效：`engine_dir` 存进去了，但工具实例**没有 `config` 属性** | 实测 `getattr(tool,'config')` → `NO_ATTR` |
| `K:\video engine` 能直接用吗 | 不能：适配层只认 `_build/audit.py` + `_build/pv/build.py` | 实测报错「缺少审计脚本：K:\video engine\_build\audit.py」 |

一句话：**这不是"再加两个工具"的活，是"把适配层从旧仓库布局解耦 + 把能力面从 3 项扩到 9 项 + 把可见性从 1 个 Agent 扩到 4 个"的活。**

---

## 1. 现状实测（2026-10-23，真机）

### 1.1 加载与注册

```
PluginLoader(plugin_dir=J:\Clipwright\plugins).load("pv_engine")
→ tools:  pv_engine_probe / pv_engine_audit / pv_engine_stills
→ skills: pv_engine_review
→ 权限档：read / read / write_local（capability_matrix.py:109-112）
→ 可选插件白名单：OPTIONAL_PLUGIN_TOOLS（capability_matrix.py:120-124）
```

结论：注册链路、权限档、能力矩阵三处都是对的，v1 的工程质量没问题。**问题全在"接出去的那一段"和"接进来的那一段"。**

### 1.2 三条工具的真实行为

| 工具 | 未配置时 | 配置成 `K:\video engine` 时 | 根因 |
|---|---|---|---|
| `pv_engine_probe` | `error: 未配置 PV 引擎目录` | `error: 缺少审计脚本：K:\video engine\_build\audit.py` | `adapter.py:26-28` 写死 `_build/` |
| `pv_engine_audit` | 同上 | 同上 | 同上 |
| `pv_engine_stills` | 同上 | 同上 | 同上（`stills_args` 用 `-m pv.build stills`） |

`K:\video engine` 是**抽出来的通用引擎**（`engine/` 包 + `python -m engine.cli`），没有 `_build/`；`_build/` 是原仓库 `eternal-questioning-pv` 的内部布局。**适配层把"一个片子的内部目录"当成了"引擎的公开契约"。**

### 1.3 配置页为什么没用

```
loader: plugin.config = merged_config          （loader.py:273）
        ↑ 只注入到插件实例
tools:  contributions() 里 new 出来的独立对象   （main.py:237-240）
        ↑ 从未收到 config
```

三条工具的 `_engine(getattr(self, "config", None))` 因此永远拿到 `None`；`engine_available()` 也只读环境变量 `CLIPWRIGHT_PV_ENGINE_DIR`，**连 config 参数都不接收**（`main.py:244-251`）。

测试没有抓到，是因为测试自己手工赋了值：`test_pv_engine_plugin.py:170,180,189,199,246` 全是 `tool.config = {...}`——**测试在替生产代码补一个它没做的动作。**

### 1.4 谁能调用：逐 Agent 核对

| 调用方 | 有没有 LLM tool-use 循环 | pv_engine 是否可见 | 证据 |
|---|---|---|---|
| 主 Agent（Kernel） | 有（`tool.surface`） | ✅ 4 项（工具按档位 + 技能整体入面） | `tool/surface.py:107-152,155-195`；实测 `build_surface()` |
| StructureAgent | 有（唯一一个动态工具面） | ⚠️ 仅当 `is_configured()` 为真；当前为假 → 0 项 | `agents/structure_agent.py:478-526`；实测 `list_agent_callable()` |
| RequirementsAgent（brief 生成） | 有，但**只在 `WebSearchService().is_configured()` 为真时装配**，且工具集是"全部 agent_callable" | ⚠️ 偶然可见；executor 不做白名单 | `services/requirements_service.py:1568-1590` |
| MaterialAgent | 无 | ❌ | 只硬编码调用 `frame_validator`、`semantic_match` |
| EditAgent | 无 | ❌ | 只硬编码调用 `video_*` / `subtitle_burn` |
| AnimationAgent | 无 | ❌ | 只硬编码 MG 渲染 |
| AudioAgent | 无 | ❌ | 只硬编码 `bpm_detect` + 一个技能 |
| QualityAgent | 无 | ❌ | 只硬编码 `frame_validator` |
| 子 Agent（SubAgentRunner） | **完全没有工具通道** | ❌ | `services/_subagent_body.txt` 全文无 tool 相关 |

**这是本次 PRD 最重要的一条**：把插件能力补齐了，如果只有主 Agent 能调，
那它仍然是"聊天里的一个玩具"，进不了 6 阶段管线。

---

## 2. 引擎能力 → 插件覆盖度（差集）

引擎侧能力按"能不能被外部命令/数据触发"分三档：
- **工具级**：有独立 CLI 入口，可以包成 Tool；
- **数据级**：只能写进 `project.json`，需要 schema 让 LLM 知道它存在；
- **不可达**：纯内部函数，只有改引擎才能用（本期不做）。

| # | 能力簇 | 引擎实现 | 现状 | 缺口形态 |
|---|---|---|---|---|
| 1 | 项目脚手架 `init`（lyric/slides/overlay 三预设） | `cli.py:65-75`、`PRESETS:18-62` | ❌ 无 | 工具级 |
| 2 | 项目体检 `info`（画幅/帧数/音轨/镜头数/cue 数/图层直方图） | `cli.py:92-107` | ⚠️ 半 | 插件自己读 JSON 算镜头数与覆盖（`main.py:64-100`），**没有画幅、帧数、图层直方图、音轨** |
| 3 | 规则审计 `check`（R01–R11） | `audit.py:34-118` | ✅ | 只认 `_build/audit.py`；输出是**人类文本**，插件原样透传 stdout，Agent 只能看字符串 |
| 4 | 抽静帧 `stills`（`still_%07.2f.png`） | `render.py:90-101` | ✅ | 同上布局问题；产物不回落到 Clipwright 资产 |
| 5 | 整片渲染 `render`（nvenc/x264/prores，workers/crf/start/end） | `render.py:31-87`、`cli.py:118-128` | ❌ 无 | 工具级；文档 §四 明确"等需要时再加" |
| 6 | 拍点网格 `beats`（bpm/period/beats[]） | `audio.py:beat_grid`、`cli.py:131-138` | ❌ 无 | 工具级 |
| 7 | 逐字对齐 `align`（whisper → 每字 t0） | `audio.py:align` | ❌ 无 | 工具级 |
| 8 | 拍点自动重音 `set_accents` | `audio.py:set_accents` | ❌ 无 | 数据级（写进 shot.camera.accents） |
| 9 | **文字排版语法**：15 个 `LAYOUTS` 锚点、25 个 `kind` 分支、`vertical` 竖排开关、`stagger/track_em/slope/drift/sink/segs/retreat/erase` | `block.py:16-32,67-103,212-536` | ❌ 无 | 数据级（**Agent 不知道字段存在，等于没有**） |
| 9b | `glyph` 逐字规格（`你×1.2↑4` 这种写法） | `typespec.py:75 parse_glyphs` | ❌ **不可用** | 解析器存在但**未接入** `project.load/expand_shot/_cue`；`block._chars:304` 只接受已解析的 **dict 列表**，写字符串直接炸 |
| 10 | 2.5D 相机：10 个预设 + `keys/aperture/max_blur/handheld/beats/accents/nudges` + 4 种缓动 | `project.py:28-66`、`camera.py:42-176` | ❌ 无 | 数据级 |
| 11 | 换场：`dir/za/zb/rack/light` 可叠加 + `seam/iris/smear/cover/xf` 原语 | `link.py:104-140`、`frames.py` | ❌ 无 | 数据级 |
| 12 | 底板与调色：`proc:` 7 类（gradient/hatch/paper/stars/smoke/bars/grid）+ 8 个内置 palette + 5 种 `bg_mode` + 自定义 palette | `assets.py:101-142`、`scene.py:9-20` | ❌ 无 | 数据级 |
| 13 | 粒子：dust/smoke/sparks/paper/shaft | `particles.py` | ❌ 无 | 数据级 |
| 14 | 2.5D 图层：**7 种** `t`（bg/figure/prop/type/fx/**map/texture**），`figure/prop` 带 `yaw/pitch/exp/x1/y1/h1/tilt1/tin/dev_*` 关键帧 | `puppet.py:114-429` | ❌ 无 | 数据级（README 只写了 5 种，`map/texture` 完全没文档） |
| 15 | 后期：`scan/glitch/bloom/halation/grain/vignette` + `grade{amount,contrast,lift}` + `trans{flash,streak,whip,tint_jump}` | `scene.py:200-300`、`trans.py` | ❌ 无 | 数据级 |
| 16 | 确定性（`t → frame` 纯函数） | 全引擎设计 | ❌ 无 | 无验证手段（可加逐帧哈希） |
| 17 | **与 Clipwright 的桥**：Timeline ⇄ `project.json`；成片/stills → 资产 | 不存在 | ❌ 无 | 工具级（这是"接进来"的另一半） |

统计：**工具级 6 项缺 4 项（init/render/beats/align），数据级 7 簇全部为零，桥 1 项为零。**
插件当前覆盖度 ≈ 引擎能力的 **2/15 ≈ 13%**。

### 2.1 文档与实现不一致（doctor 必须"以代码为准"的理由）

README/SKILL 是给人看的，**有 5 处与代码不符**。任何"照文档抄进 schema"的工具都会把 Agent 引向坏路径：

| # | 文档怎么说 | 代码事实 | 影响 |
|---|---|---|---|
| D1 | `layout: fill_low` 合法（README:101、SKILL:60） | `LAYOUTS` 无此键（`block.py:16-32`），静默回退 `center` | 竖屏字幕写 `fill_low` → 跑到画面正中间 |
| D2 | `kind: vertical` 是文字动效之一（SKILL:61） | 竖排由 `cue.vertical: true` 触发（`block.py:101`），`kind=vertical` 无效 | 竖排字幕不出竖排 |
| D3 | `glyph: "你×1.2↑4"` 字符串（README:107） | `_chars` 只吃 **dict 列表**；`parse_glyphs` 未接线 | 写字符串抛异常（不是"效果不对"） |
| D4 | `trans_in` 支持 flash/streak/whip/tint | `expand_shot:156-157` 不拷贝 `trans_in`/`tr_out` → 项目文件不可达（死代码） | 文档承诺的转场永远不出现 |
| D5 | 30+ 条审计规则（README:158-172） | engine 版只有 **R01–R11**（`audit.py:34-118`），30+ 是上游 PV 仓库的 | 「30 条全过」的乐观预期不成立 |

> 结论：**F4 的 doctor 只能反射代码，不能抄 README**；同时 `docs/pv-engine-plugin.md` 与引擎 README 需要一份"已知不一致"清单（见 F4b）。

### 2.2 三个"看着能用、实际是坑"的写法

| 写法 | 为什么是坑 | 判定依据 |
|---|---|---|
| 写 `raw cam dict`（带 `keys`/`beats`）但不写 `handheld/aperture/max_blur` | 落到 `Cam.__init__` 默认值 `1.0/3.2/6.0`，**不是预设的 `0.35/1.6/4.0`** → 画面抖到不能看、景深糊一倍 | `camera.py:55-57` vs `project.py:54-60` |
| `post: {"glitch": 0.8}` 与 `shot.glitch: [0.2, 0.9]` | 同名不同义：前者是后期 0–1 倍率，后者是两元素故障起止；写错静默无效果 | `scene.py:335` vs `scene.py:337` |
| `layers` 直写时用 `map`/`texture` | 代码支持 **7 种** `t`（README 只写 5 种），但 `map/texture` 的字段与 `figure/prop` 完全不同，凭直觉写必然错位 | `puppet.py:208-265,390-429` |

> 这三条不修引擎，只能在 doctor 的 `examples` 区各给一段"正确写法 + 错误写法对比"，否则 Agent 一定会踩。

### 2.3 与字幕缺陷报告的交叉引用

同批审计产出了《[字幕链路 BUG 探查报告](subtitle-bug-report-2026-10-23.md)》（24 条，含引擎侧 10 条）。
其中 3 条**直接决定本 PRD 能承诺什么**，必须一起看：

| 缺陷 | 对 PRD 的影响 |
|---|---|
| S01 标点使逐字时间错位（`block.py:213` vs `project.py:81-88`） | `timeline_to_project`（F7）不能再声称"逐字时间保真"；要么先修引擎，要么在工具返回里带 `word_timing_warning` |
| S03 无像素换行、长句缩到 36px | 竖屏字幕默认走引擎是**画质负收益**；F7 需给出"超过 N 字时仍走 ASS"的门槛 |
| S04 审计字号与渲染字号不同口径 | F9 的结构化审计要标注 `px_source: "declared"`，不能让 Agent 以为那是渲染值 |

其余 7 条（S02/S05–S10）属"引擎内部质量问题"，本期**只披露不修**，统一进入 F4b 产出的
`docs/pv-engine-known-gaps.md`，并在 `doctor().known_issues` 里返回机器可读版本。

---

## 3. 目标与非目标

### 3.1 目标（可验收）

| 编号 | 目标 | 可验证形态 |
|---|---|---|
| G1 | 引擎目录双布局可解析 | `engine_dir` 指向 `K:\video engine` 与旧仓库都能 `probe/audit/stills` 通过 |
| G2 | 插件配置真正生效 | 配置页填 `engine_dir` → `is_configured()=True` → 三条工具进阶段 Agent 工具面 |
| G3 | 能力面从 3 项扩到 9 项 | 新增 `init / doctor / render / beats / align / timeline_to_project`，全部有单测 + 至少 1 个真机用例 |
| G4 | 让 4 个 Agent 能用 | 主 Agent、StructureAgent、EditAgent、AnimationAgent 各自工具面含对应 pv 工具（见 §4.10） |
| G5 | 引擎语法对 LLM 可发现 | `pv_engine_schema()` 一次返回 layouts/kinds/cam presets/palettes/procs/字段默认值，且与引擎代码同源（不是手抄） |
| G6 | 产物回流 | `render/stills` 产物写入 `PluginData/pv_engine/<job_id>/` 并登记为 Clipwright 资产/预览图 |
| G7 | 审计结果结构化 | 每规则 `{id, level, message}` → `verdict`，前端可只显示 FAIL 行 |

### 3.2 非目标

- 不改引擎渲染算法（本 PRD 只加适配层与工具；引擎侧缺陷另见《字幕 BUG 探查报告》）。
- 不做实时预览（沿用"抽帧看构图"）。
- 不替换现有 ASS/libass 字幕渲染器；引擎排版作为**旁路能力**（PV/MV 类型优先用引擎，普通混剪继续走 ASS）。
- 不做云端渲染、不做 4K 全片。
- 不在本期把 `render` 设为 `read/write_local`：它是 `costly` 且长耗时，必须配预算闸。

---

## 4. 功能需求

### F1 引擎定位双布局（P0）

`adapter.resolve_engine()` 返回的 `EnginePaths` 改为**探测型**：

```python
@dataclass(frozen=True)
class EnginePaths:
    root: Path

    @property
    def kind(self) -> str:
        """'package'（engine/ 包，python -m engine.cli）| 'build'（旧仓库 _build/）"""
    @property
    def validate(self) -> str:          # 空串=可用
```

探测顺序：
1. `root/engine/cli.py` 存在 → `kind="package"`，命令前缀 `[python, "-m", "engine.cli"]`，cwd=`root`；
2. `root/_build/audit.py` + `root/_build/pv/build.py` 存在 → `kind="build"`，命令前缀 `[python, "-m", "pv.build"]` / `[python, "audit.py"]`，cwd=`root/_build`；
3. 都不满足 → 返回**两条候选原因**（不是只报第一条）：
   `未识别为引擎目录：既没有 engine/cli.py，也没有 _build/audit.py。`

**验收**
- `engine_dir=K:\video engine`：`probe/audit/stills` 全部 `status=success`。
- `engine_dir=<旧仓库>`：行为与 v1 完全一致（回归用例保留）。
- 目录不存在时，错误文本包含"既没有…也没有…"两条。

### F2 配置注入与可见性（P0）

两条路都要修，且**必须一起修**（只修一条会出现"配置了但 agent 看不见"）：

1. **注入**：`capability` 插件在 `contributions()` 之后，由 loader 统一给每个贡献对象挂 `obj.config = merged_config`（在 `_register_declarations` 之前），或改用 `tool.config_provider = lambda: plugin.config`。选后者更稳：避免 reload 后工具持有旧 dict。
2. **判定**：`engine_available(config: dict | None = None)` 接收配置，`is_configured()` 用 `self.config_provider()` 解析，**不再只读环境变量**。

**验收**
- 配置页填 `engine_dir`（不设环境变量）→ `list_agent_callable()` 含三条工具。
- 环境变量与配置项同时存在时，`engine_dir` 优先（与 `adapter.resolve_engine` 的现有优先级一致）。
- 新增回归用例：`test_config_from_plugin_config_not_env`（当前测试用手工赋值掩盖了这条）。

### F3 `pv_engine_init`（P0，工具级）

包 `engine.cli init DIR --preset lyric|slides|overlay [--force]`。

- `dir` 默认 `<workspace>/pv/<slug>`；`--force` 默认 False（防覆盖）。
- 返回 `{project_path, preset, shots, duration}`，并把 `project.json` 读回来做一次结构自检（见 F4）。
- 权限档 `write_local`。

**验收**
- 三个 preset 各跑一次，产物能被 `pv_engine_audit` 读取（0 FAIL 或仅 WARN）。
- `--force=false` 且目标存在时，返回 `error` 而非覆盖。

### F4 `pv_engine_doctor`（P0，工具级 + 契约源头）

一次性回答"引擎能干什么"，**内容必须从引擎代码反射生成，不许手抄**：

| 区块 | 来源 | 字段 |
|---|---|---|
| `layouts` | `block.LAYOUTS` | 15 个键 + `{x,y,s,align}`（**不含 `fill_low`**） |
| `kinds` | `block.draw()` 的分派表 + `_chars` 的逐字分支 | 特殊 13 + 逐字 16，并对 `vertical` 单独标 `flag`（不是 kind） |
| `glyph_format` | `block._chars:304` 的实际期望 | `"list[dict]"`，附一条合法样例；字符串写法标 `unsupported` |
| `cam_presets` | `project.CAM_PRESETS` | 10 个键 + 展开后的 `handheld/aperture/max_blur`（并注明"写 raw cam dict 时默认值是 1.0/3.2/6.0，不是预设值"） |
| `palettes` | `scene.PALETTES` | 8 个键 + lo/hi |
| `procedural` | `assets.procedural()` 的 kind 分支 | 7 类 |
| `bg_modes` | `scene.plate_variant` | plate/mosaic/ink/light/ghost |
| `layer_types` | `puppet.py:390-429` 的绘制分派 | **7 种**（bg/figure/prop/type/fx/**map/texture**）+ z 默认值 |
| `post/grade` | `project.POST_DEFAULT` / `GRADE_DEFAULT` | 字段 + 默认值（并标注 `post.glitch` 与 `shot.glitch` 同名不同义） |
| `cue_fields` | `project._cue()` 的 setdefault 集合 | 字段 + 默认值 |
| `audit_rules` | `audit.check()` 的 add() 调用 | R01–R11 + 阈值来源（**不是 30+**） |
| `cli` | `cli.main()` 的 subparser | 子命令 + 参数 |
| `dead_fields` | `expand_shot` 不拷贝但下游会读的键 | `trans_in`/`tr_out`/`motion`/legacy 字段——显式标 `unreachable`，防止 Agent 反复尝试 |

实现方式：**子进程跑一段 `python -c` 把 JSON 吐出来**（`engine` 包可 import），不走文本解析。
引擎不可用时返回"不可用 + 原因"，**不返回手抄的过期快照**（宁可空，不可假）。

**验收**
- `doctor()` 的 `layouts` 键集合 == `set(block.LAYOUTS)`（同一进程内断言）。
- 引擎目录指错时，返回 `available=false` 且 `reason` 非空。
- `doctor()` 里**不出现** `fill_low`、不把 `vertical` 列进 `kinds`、`glyph_format` 标 `list[dict]`——这三条是 §2.1 的一致性回归断言。

### F4b 逐字规格（`glyph`）接线与文档校准（P1）

现状：`typespec.parse_glyphs()` 能解析 `你×1.2↑4 看×1.0↓2`，但 `project._cue` 从不调用它，
于是文档承诺的写法在真机上抛异常。两种修法，**选后者**：

- ~~改引擎接线~~（要动引擎仓库，且 `typespec.ACTIONS` 26 个动作里只有 8 个在 `_chars` 有实现，接了也是半个）；
- **适配层接线**：`timeline_to_project` / 任何接收 cue 写法的工具，先把字符串交给 `typespec.parse_glyphs` 转 dict 列表，再落盘；并如实返回 `actions_implemented: ["slam","stamp","land","lift","wipe","tremble","cut","burn"]`、`actions_ignored: [...]`。

同时产出 `docs/pv-engine-known-gaps.md`（D1–D5 + 死字段清单），并在 `pv-engine-plugin.md` 顶部加一行指向它。

**验收**
- 传 `glyph="你×1.2↑4"` → 生成的 `project.json` 里 `cue.glyph` 是 `[{"s":1.2,"dy":...}]` 形式的列表，且 `pv_engine_audit` 通过。
- 传 `glyph` 里含未实现动作（如 `copy`）→ 返回 `warning` 列出被忽略的动作，**不静默丢弃**。

### F5 `pv_engine_render`（P1，工具级，`costly` + 预算闸）

包 `engine.cli render`。

- 入参：`shots, out, workers=8, crf=18, codec=nvenc|x264|prores, start=0, end=0, max_frames, max_wall_sec`。
- **预算闸**：默认 `max_frames=3600`（120s@30fps）、`max_wall_sec=1800`；超限直接拒绝并给出"先用 stills 定稿"的提示。
- 长任务：走后台 job（复用 `render queue` 的 job 语义），工具返回 `job_id` + 轮询端点，不阻塞 agent 回合。
- 权限档 `costly`（不是 `dangerous`：它只写项目目录内产物）；`cost_usd=None` 时 surface 记"成本未知"（已有机制，`surface.py:305-320`）。
- 无 ffmpeg / 无 `h264_nvenc` 时：`codec=nvenc` 自动降 `x264` 并在 `warning` 里写明（不静默）。

**验收**
- `examples/demo`（8s/24fps）用 `codec=x264, workers=4` 渲出 mp4，`ffprobe` 时长误差 ≤1 帧。
- `end=0` 视为全片；`start=N` 时音轨同步（`render.py:44-46` 已有 `-ss`）。
- 超 `max_frames` 时返回 `status=error` 且**不产生部分文件**。

### F6 `pv_engine_beats` / `pv_engine_align`（P1，工具级）

- `beats(audio, out)` → `{bpm, period, beats[], out_path}`；无 scipy 时返回依赖缺失（`audio.py:onset_env` 需要 scipy）。
- `align(text, audio, language, model="small")` → `words[{t0, text}]`；whisper 未安装时返回 `dependency_missing`，提示"用 beats + 手工铺"。

**验收**
- 用 `examples/demo/song.wav`（1.4MB）跑 `beats`，`bpm` 落在合理区间（60–200），`beats` 单调递增。
- `align` 缺 whisper 时返回 `dependency_missing`，不抛异常。

### F7 `pv_engine_timeline_to_project`（P0，工具级 + 桥）

把 Clipwright `Timeline` 转成引擎 `project.json`（单向，先做这一个方向）：

| Timeline | project.json |
|---|---|
| `width/height/fps` | `meta.width/height/fps` |
| `duration_sec` | `meta.duration` |
| `kind=caption|text` 的 clip | `shots[i].text[]`：`t0=start_sec, t1=start+duration` |
| `clip.font_size` | `cue.px`（缺失则留空，让引擎按 `band` 反算） |
| `clip.font_color` | `cue.color` |
| `clip.text_align` + `metadata.position` | `cue.layout`（bottom+center→`low_center`，top+center→`up_center`，…） |
| `clip.metadata.pv.{kind,band,stagger,glyph,tracking,glow}` | 同名 cue 字段（**命名空间隔离，避免污染通用 Timeline**）；`glyph` 字符串经 F4b 转 dict 列表 |
| 同一时间段内所有 caption/text | 归并成 **一个 shot**（引擎是"一镜多 cue"，不是一 cue 一镜） |

反向（`project → Timeline`）本期**不做**，但要在 `doctor` 里写明"单向"。

**验收**
- 给一条含 3 条 caption 的 Timeline，导出的 `project.json` 能被 `pv_engine_audit` 通过（0 FAIL）。
- 相邻 caption 的 `(t0,t1)` 与原名一致（误差 <0.01s）。
- `metadata.pv` 缺失时使用默认值，不写 `null`。

### F8 产物回流（P1）

- `render/stills` 的输出统一落 `PluginData/pv_engine/<job_id>/`，返回 `{files[], job_id, out_dir}`。
- `stills` 额外返回 `preview_urls[]`（走 `/srv` 或 `/api/asset` 的静态映射），供前端"构图复核"直接显示。
- `render` 产物登记为 Clipwright 资产（`source=pv_engine`, `sha256`, `duration`），可在素材库里检索。

**验收**
- `stills` 后前端能直接看到图（不需要用户手点路径）。
- 同一 `job_id` 重复调用命中缓存（文件已存在且 mtime 一致），不重复渲染。

### F9 审计结构化（P0）

`audit` 的 stdout 当前是 `[FAIL] R05 文字不出画/参数在界内 ...`（`audit.py:113-117`）。在适配层做一次解析：

```json
{"verdict": "fail",
 "rules": [{"id": "R05", "level": "FAIL", "message": "镜@0.00 「...」越出镜头"}],
 "counts": {"FAIL": 1, "WARN": 2, "OK": 8},
 "raw": "..."}
```

解析失败时 `raw` 照旧返回，`rules=[]`，并标 `parsed=false`（不许把解析失败当"全过"）。

**验收**
- 现有 `examples/overlay`（1 条 WARN）→ `counts.WARN=1, verdict=pass`。
- 人为造一条 R05 → `verdict=fail` 且 `rules[0].id=="R05"`。
- 解析器有独立单测（喂 3 段不同格式的 stdout 样本）。

### F10 多 Agent 接线（P0，本次的核心）

**不做"把 45 个工具的 agent_callable 全翻 True"那种事**（`tool/surface.py:4-6` 已经写明这条纪律）。
按**职责**给最小工具集，且每个 Agent 的接入方式不同：

| Agent | 接入方式 | 该给的工具 | 理由 |
|---|---|---|---|
| 主 Agent（Kernel） | 已有 `tool.surface` | 全部（按档位） | 已经在面内，只需 F1/F2 修好就真的能用 |
| StructureAgent | 已有 `list_agent_callable()` | `doctor`（read）、`init`（write_local）、`audit`（read） | 结构化阶段需要"引擎会什么"来决定用不用 PV 语法 |
| EditAgent | **新增**一条最小 tool-use 通道（见下） | `timeline_to_project`、`stills` | 剪辑阶段是唯一同时握有 Timeline 与画面判断的 Agent |
| AnimationAgent | 同 EditAgent | `stills`、`doctor` | MG/文字动画要看静帧复核 |
| QualityAgent | **不加工具**，改为：`audit` 结果作为管线质检的一项硬指标 | — | 质检不该自己造项目，只该消费结论 |
| Material / Audio / Requirements | 不加 | — | 与 PV 引擎无职责交集 |

**EditAgent / AnimationAgent 的最小通道**（不引第二套框架）：

```python
# agents/base.py 新增
async def _with_tools(self, tools: list[str], ...):
    """只在显式声明的工具集上开一轮 tool-use；其余工具不可见。"""
```

- 白名单来自 Agent 的类属性 `pv_tools: tuple[str, ...]`，默认空（现有 5 个 Agent 行为零变化）。
- 每个工具调用都过 `ToolRegistry.execute`（审计/路径校验已有）。
- 轮数上限 3 轮、工具调用上限 6 次，超限落回原逻辑（不可用就当没有）。
- **失败必须可降级**：EditAgent 拿不到引擎时，继续走现有 ASS 字幕路径，不许整条管线失败。

**验收**
- 单测：`EditAgent.pv_tools` 为空时，`_with_tools` 不被调用（行为与今天逐字节一致）。
- 单测：`EditAgent` 在白名单下收到 `stills` 的假结果，能把路径写进 `clip.metadata.preview_still`。
- 集成：引擎未配置时跑一次 6 阶段管线，`QualityAgent` 报告里出现 `pv_engine: skipped(未配置)` 而不是报错。

### F11 前端（P2）

| 位置 | 内容 |
|---|---|
| 插件配置页 | `engine_dir` 旁加"检测"按钮 → 调 `doctor` 显示 `kind=package/build` 与缺失项（当前填错只有运行时才知道） |
| 素材库/预览 | `stills` 产物的 `preview_urls` 直接出图 |
| PV 面板（可选） | `doctor` 的 layouts/kinds 列表做成下拉，选中即插入 `metadata.pv` 片段（**不写自由文本 JSON**） |

`J:\Clipweight-Client` 侧本期只需第一项；后两项不做也不阻塞。

---

## 5. 数据契约

### 5.1 `project.json`（引擎侧，v1 不变）

```jsonc
{"meta": {"width":1920,"height":1080,"fps":24,"safe":96,"duration":12,"out":"out/film.mp4"},
 "audio": "song.wav",
 "assets": {"root":"assets","map":{"lake":"assets/lake.jpg"}},
 "fonts": {"serif":"C:/Windows/Fonts/NotoSerifSC-VF.ttf"},
 "palettes": {"mine":{"lo":[16,10,8],"hi":[230,200,150]}},
 "defaults": {"palette":"sepia","post":{"scan":0.25,"bloom":0.18}},
 "shots":[{"t0":0,"t1":4,"bg":"proc:stars","bg_mode":"plate","camera":"push",
           "transition":{"dur":0.5,"dir":"left","rack":10},
           "text":[{"text":"第一句","t0":0.5,"t1":3.4,"layout":"center",
                    "kind":"band","band":0.66,"px":120,
                    "glyph":"你×1.2↑4 看×1.0↓2"}]}]}
```

### 5.2 Clipwright 侧命名空间（新增，向后兼容）

`Clip.metadata.pv` —— 因为 `Clip.model_config = {"extra": "allow"}`（`schema/timeline.py:184`），
**不加 schema 字段也能落库**；本期只在文档与工具体里约定键名：

```jsonc
"metadata": {
  "category": "caption",
  "pv": {"kind":"band","band":0.66,"layout":"low_center",
         "stagger":0.0,"tracking":0.02,"glow":0.45,
         "glyph":"你×1.2↑4",        // 字符串只在 Clipwright 侧；落盘前由 F4b 转成列表
         "enabled": true}
}
```

落盘后的 `project.json` 里 `cue.glyph` 必须是 **dict 列表**（`[{"s":1.2,"dy":-4},…]`），
因为 `block._chars:304` 只认这个形态（§2.1 D3）。

约定：**`metadata.pv` 存在且 `enabled!=false` 时，`timeline_to_project` 才把该 clip 交给引擎**；
否则仍走 ASS 渲染器。这样"引擎化"是**逐 clip 开关**，不是全局切换。

### 5.3 工具签名（本期定稿）

```python
pv_engine_doctor()                                              -> {...}
pv_engine_init(dir="", preset="lyric", force=False)              -> {...}
pv_engine_probe(shots, engine_dir="")                            -> {...}   # 保留
pv_engine_audit(shots, video="", scan=False, engine_dir="")      -> {...}   # 保留
pv_engine_stills(times, shots, out_dir="pv_stills", engine_dir="")-> {...}  # 保留
pv_engine_render(shots, out="", codec="nvenc", crf=18, workers=8,
                 start=0, end=0, max_frames=3600, max_wall_sec=1800, engine_dir="")
pv_engine_beats(audio, out="", engine_dir="")
pv_engine_align(audio, text, language="", model="small", engine_dir="")
pv_engine_timeline_to_project(timeline, out="", shots_from="captions", engine_dir="")
```

技能层保留 `pv_engine_review`，并新增 `pv_engine_authoring`（doctor → init → timeline_to_project → audit → stills 一条龙，供 StructureAgent 一次调用）。

---

## 6. 权限与成本

| 工具 | 档位 | 幂等 | dry_run | 说明 |
|---|---|---|---|---|
| `doctor` | read | 是 | 否 | 纯反射 |
| `init` | write_local | 否 | 是（列出将创建的路径） | 不覆盖已有 project |
| `probe`/`audit` | read | 是 | 否 | 现有 |
| `stills` | write_local | 否 | 否 | 可缓存 |
| `render` | **costly** | 否 | 是（只算帧数/预估耗时） | 预算闸必配 |
| `beats`/`align` | costly | 否 | 否 | 依赖 scipy/whisper |
| `timeline_to_project` | write_local | 是 | 是 | 纯数据变换 |

`TIER_MAP` 与 `OPTIONAL_PLUGIN_TOOLS` 同步新增 5 条（否则 `capability_matrix` 的强校验会报"工具未裁档位"，`capability_matrix.py:196-224`）。

**红线**：`render` 不进 `dangerous`（它不执行任意命令，只调固定 CLI，参数全为白名单枚举/数字）；
`engine_dir` 必须过 `assert_allowed_path`（当前工具完全没做路径校验——`ToolRegistry.execute` 只校验 `path/file_path` 等键名，`engine_dir`/`shots` 不在其中）。**这条要单独修**。

---

## 7. 分期

| 阶段 | 内容 | 人日 | 交付判据 |
|---|---|---|---|
| P0-a | F1 双布局 + F2 配置注入 + F9 审计结构化 + 路径校验 | 2 | 三条旧工具在 `K:\video engine` 上真机通过；配置页生效 |
| P0-b | F4 doctor + F3 init + F7 timeline_to_project + F4b glyph 接线 | 3 | StructureAgent 能自主决定"用不用 PV"并生成可审项目 |
| P1-a | F5 render（预算闸 + 后台 job）+ F6 beats/align | 3 | 8s demo 渲出 mp4；拍点/对齐可用 |
| P1-b | F10 多 Agent 接线（EditAgent/AnimationAgent/QualityAgent） | 3 | 引擎缺失时管线不失败；引擎可用时 stills 进 clip.metadata |
| P2 | F8 产物回流 + F11 前端 | 2 | 前端一键看到静帧 |

合计约 13 人日。**P0-a 单独可上线**（不动架构，只修适配层与配置）。

> 顺序硬约束：F4b（glyph 接线）必须晚于 F4（doctor）——doctor 要先能读出 `actions_implemented`，接线才知道该警告哪些动作。

---

## 8. 验收（可执行）

```powershell
# 1) 双布局
$env:CLIPWRIGHT_PV_ENGINE_DIR = "K:\video engine"
python -m pytest tests/clipwright/test_pv_engine_plugin.py -q

# 2) doctor 与引擎同源
python -c "from plugins.pv_engine.main import PvEngineDoctorTool as T; import json; \
print(json.loads(__import__('asyncio').run(T().execute()).output['json'])['layouts'].keys())"

# 3) 真机三连
python -m engine.cli check examples/demo/project.json       # 期望 0 FAIL
# 经插件：init → audit → stills → render(x264, 8s)

# 4) 可见性
python -c "from clipwright.tool import register_builtin_tools; register_builtin_tools(); \
from clipwright.plugins.loader import PluginLoader; from pathlib import Path; \
PluginLoader(plugin_dir=Path('plugins')).load('pv_engine'); \
from clipwright.tool.registry import ToolRegistry; \
print([t.name for t in ToolRegistry.list_agent_callable() if t.name.startswith('pv_')])"
# 期望：doctor/init/audit/probe/stills/timeline_to_project 均在列（render/beats/align=costly 不入阶段面）

# 5) 管线降级
python -m pytest tests -q -k "pipeline and pv"
```

---

## 9. 风险与不做

| 风险 | 处置 |
|---|---|
| 引擎目录是"用户机器上的任意路径" | `assert_allowed_path` + 只读探测；不允许写引擎目录 |
| `render` 让人误以为"点一下出片" | surface 描述里写死 `[costly]`，并返回 `est_frames/est_sec` |
| 两套字幕渲染器并存导致"预览/成片不一致" | `metadata.pv.enabled` 逐 clip 开关 + `doctor` 明示"引擎路径不走 ASS" |
| `timeline_to_project` 的字段映射漂移 | 映射表进代码常量 + 单测双向断言（`caption→cue`） |
| 引擎语法（25 个 kind）对 LLM 是噪声 | 只把 `doctor` 的**摘要**（名称 + 一句话）入 prompt，详细字段按需二次调用 |

**明确不做**：引擎侧并发渲染、GPU 资源调度、4K 全片、`project → Timeline` 反向、把引擎排版移植进 libass。

---

## 10. 实施状态（2026-10-23 当日落地）

| 需求 | 状态 | 落点 |
|---|---|---|
| F1 双布局 | ✅ | `plugins/pv_engine/adapter.py`（`EnginePaths.layout` 探测 package/build；命令与 cwd 按布局切换） |
| F2 配置注入 | ✅ | `plugins/loader.py` `_inject_plugin_config`（注册后注入 + `save_config` 热更新 + `on_plugin_config` 钩子）；三工具 `is_configured()` 改读配置 |
| F3 init | ✅ | `PvEngineInitTool`（build 布局明确拒绝） |
| F4 doctor | ✅ | `run_doctor()` + `DOCTOR_SCRIPT`（AST 反射 kinds/procedural/bg_modes/layer_types/audit/cli/cue_defaults；导入 LAYOUTS/CAM_PRESETS/PALETTES/defaults）；build 布局返回"不支持"而不是假快照 |
| F4b glyph | ✅ | `_glyph_parse()`：package 布局走 `engine.typespec.parse_glyphs` 子进程，失败退本地最小解析并给 `warning` |
| F5 render | ✅（同步版） | `PvEngineRenderTool`：帧数预算（默认 3600）+ 墙钟预算（1800s），超限不落文件；**后台 job 语义未做**（差异见下） |
| F6 beats/align | ✅ | `PvEngineBeatsTool` / `PvEngineAlignTool`（缺 scipy/whisper 如实报错） |
| F7 timeline→project | ✅ | `_collect_caption_clips` + `_build_project`：cue 映射（layout/px/color/band/kind/glyph）、按不重叠 cue 群切镜并首尾相接（保 R01/R07） |
| F8 产物回流 | ✅ | 落 `PluginData/pv_engine/<job>/`；后端新增 `/pv-engine` 静态挂载（`stills` 返回 `preview_urls`、`render` 返回 `url`）；`render` 成功后经 `AssetManager.import_file` 登记素材库（sha256 去重/缩略图），返回 `asset`，登记失败只记 `asset_error` 不影响出片 |
| F9 审计结构化 | ✅ | `parse_audit()` + `verdict` 四态（`pass/pass_with_warnings/fail/error`）+ `px_source: "declared"` |
| F10 多 Agent 接线 | ✅（机制 + 两处接线） | 新增 `clipwright/tool/capabilities.py` + `BaseAgent`（`resolve_tool/call_tool/call_skill/tool_schemas/run_tool_loop/capability_report`）；Edit/Animation/Quality 已接；Material/Audio 改为能力解析 |
| F11 前端 | ◐ | 插件管理页新增「自检」按钮（调 `<id>_doctor`，显示布局/可用性/能力计数）；PV 面板下拉与 stills 内嵌预览未做 |

### 10.1 与 PRD 的差异（必须知道）

1. **render 是同步调用**：没有做"后台 job + 轮询"。预算闸把影响面限制在 ≤1800s；真正的
   异步化需要动 render queue 语义，独立立项更合适。
2. ~~产物未登记资产~~：已补（见 F8）；登记失败不阻断出片。
3. **PV 面板 UI 未做**：doctor 的 layouts/kinds 只在自检弹窗里显示，没有做成"选中即插入 metadata.pv"的下拉。
4. **子 Agent 仍无工具通道**：`SubAgentRunner` 是纯 LLM 委派；给它挂工具属于另一条安全边界，未在本次改动内。

### 10.2 验收结果（真机）

```text
pytest tests/clipwright/test_pv_engine_plugin.py  → 40 passed, 1 skipped
pytest tests/test_capabilities.py                 →  8 passed

engine_dir = K:\video engine（package 布局）
doctor            → available=true, layouts=15, kinds=31, audit_rules=11（R01–R11）,
                    layer_types=[bg,figure,fx,map,prop,texture,type], fill_low 不在 layouts 内
probe             → layout=package, info 输出正常
audit(overlay)    → verdict=pass_with_warnings, counts={FAIL:0, WARN:1, OK:10}
timeline_to_project→ 2 shots / 2 cues，镜头首尾相接，glyph 转成 dict 列表
audit(生成的)     → verdict=pass, counts={FAIL:0, WARN:0, OK:11}
stills            → 2 张 PNG + preview_urls=[/pv-engine/selftest3/stills/...]

前端 npx tsc --noEmit → 通过
```
