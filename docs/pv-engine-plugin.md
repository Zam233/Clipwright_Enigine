# PV 引擎插件（`pv_engine`）v2

把逐帧纯函数渲染引擎接成 Clipwright 里 **agent / 子 agent 可调用** 的能力。

- 引擎两种合法形态：**通用引擎**（`engine/cli.py`，如 `K:\video engine`）与 **上游 PV 仓库**
  （`_build/audit.py` + `_build/pv/build.py`）。插件按特征探测，两者都认。
- 引擎本身的"文档说了、代码没有"的问题见 [pv-engine-known-gaps.md](pv-engine-known-gaps.md)；
  插件只如实披露，不改引擎。

---

## 一、配置

| 项 | 说明 |
|---|---|
| `engine_dir` | 引擎根目录。留空则读环境变量 `CLIPWRIGHT_PV_ENGINE_DIR`。**插件配置页填了就生效**（v1 只挂到插件实例、工具拿不到，见 §六 修复记录） |
| `audit_timeout_sec` | 审计超时，默认 300 |
| `stills_timeout_sec` | 抽帧超时，默认 900 |
| `doctor_timeout_sec` | 内省超时，默认 120 |

管理页每个插件行有「自检」按钮：调用约定的 `<plugin_id>_doctor`，把"目录填错在哪 /
是什么布局 / 反射到多少条能力"直接显示出来，不必等到运行工具才发现。

路径解析优先级：工具入参 `engine_dir` > 插件配置 `engine_dir` > 环境变量。

产物（静帧/成片/项目文件）统一落 `PluginData/pv_engine/<job_id>/`，并由后端以
`/pv-engine/*` 静态挂载提供可访问 URL（`stills` / `render` 返回 `preview_urls` / `url`）。

---

## 二、贡献的能力

| 工具 | 档位 | 幂等 | 干什么 |
|---|---|---|---|
| `pv_engine_doctor` | read | 是 | **反射引擎代码**：layouts / kinds / 相机预设 / 调色板 / 程序化底板 / 图层类型 / 审计规则 / CLI 契约 + `known_gaps`。目录填错时也返回原因（**不做配置门控**） |
| `pv_engine_init` | write_local | 否 | 脚手架 `project.json`（lyric/slides/overlay） |
| `pv_engine_probe` | read | 是 | 体检 shots 数据（镜头数/覆盖/空隙） |
| `pv_engine_audit` | read | 是 | 规则审计，**结构化**：`{verdict, counts, rules[{id,level,message}]}`；`verdict` 取值 `pass / pass_with_warnings / fail / error` |
| `pv_engine_stills` | write_local | 否 | 抽静帧（返回 `still_files` + `preview_urls`） |
| `pv_engine_render` | **costly** | 否 | 整片渲染；**预算闸**：`max_frames`（默认 3600）、`max_wall_sec`（默认 1800），超限直接拒绝且不产半成品 |
| `pv_engine_beats` | **costly** | 否 | 拍点网格（bpm/period/beats） |
| `pv_engine_align` | **costly** | 否 | 文案→逐字时间（whisper，缺依赖如实报错） |
| `pv_engine_timeline_to_project` | write_local | 是 | Clipwright `Timeline` → `project.json`（caption/text → cue；`metadata.pv` 命名空间；`glyph` 字符串代转 dict 列表） |

| 技能 | 干什么 |
|---|---|
| `pv_engine_review` | probe → audit（→ stills）一步到位，带 `summary` |
| `pv_engine_authoring` | doctor → timeline_to_project（或 init）→ audit（→ stills）一条龙 |

### 工具签名

```python
pv_engine_doctor()
pv_engine_init(dir="", preset="lyric", force=False)
pv_engine_probe(shots, engine_dir="")
pv_engine_audit(shots, video="", scan=False, engine_dir="")
pv_engine_stills(times, shots, out_dir="", job_id="", engine_dir="")
pv_engine_render(shots, out="", codec="nvenc", crf=18, workers=8,
                 start=0, end=0, max_frames=3600, max_wall_sec=1800, engine_dir="")
pv_engine_beats(audio, out="", engine_dir="")
pv_engine_align(text, audio, language="", model="small", engine_dir="")
pv_engine_timeline_to_project(timeline=None, clips=None, out="", audio="",
                              job_id="", default_bg="proc:hatch")
```

---

## 三、能力解析（Agent 不写死工具名）

工具可以声明一个**能力名**：

```python
class MyFrameChecker(BaseTool):
    name = "acme_frame_check"
    capability = "frame_check"        # 接管"帧质检"这个能力
    capability_priority = 50          # 同能力竞争时的优先级（大的先）
```

`clipwright.tool.capabilities` 在**调用时刻**解析：声明式候选（按优先级）→ 内置别名兜底。
于是插件装卸、依赖缺失、配置变化都在下一次调用立即生效，Agent 源码不用改。
`explain(capability)` 会给出候选、是否可用、被过滤原因（供排障 / 诊断页）。

---

## 四、对哪些 Agent 可见

| 调用方 | 可见性 |
|---|---|
| 主 Agent（Kernel） | 全部（按档位：`dangerous` 不入面；`costly` 带成本提示） |
| StructureAgent | 走 `list_agent_callable()`：配置就绪的 8 个工具 + 技能 |
| EditAgent | 白名单 4 个（doctor/timeline_to_project/stills/audit）；编辑阶段若有 `metadata.pv` 字幕，会抽一张静帧挂 `clip.metadata.preview_still` |
| AnimationAgent | 白名单 4 个（list_animations/describe_llm_mg/doctor/stills）；并把"实时工具/技能列表"写进 trace |
| QualityAgent | 有 pv 字幕时把引擎审计并入质检：FAIL→error、WARN→warning；未配置→info `pv_engine: skipped（未配置）` |
| Material / Audio / Requirements | 不涉及 PV（它们已改成按能力解析自己那几个工具） |
| 子 Agent | 无工具通道（既有设计） |

---

## 五、诚实边界

- 引擎仓库**不含版权素材**：`probe/audit/doctor` 可离线；`stills/render` 缺图会如实报错。
- `doctor` 的模块内省只支持 `engine/` 包布局；上游 `build` 布局明确返回"不支持"，不返回手抄快照。
- `render` 是同步调用且有预算闸；超预算的提示是"先用 stills 定稿"。
- 引擎侧已知缺陷（标点错位、无像素换行、审计字号口径……）见 known-gaps；插件不改引擎，
  只在返回里标注 `warnings` / `px_source: "declared"`。

---

## 六、v2 修复记录（v1 的真实故障）

| 问题 | 根因 | 修复 |
|---|---|---|
| 配置页填 `engine_dir` 不生效 | v1 只把 config 挂到**插件实例**，`contributions()` 里 new 出来的 Tool 永远没有 `config` 属性 | `loader` 在注册后把配置注入工具/技能（`_inject_plugin_config`），并支持 `on_plugin_config` 钩子；`save_config` 热更新也同步 |
| `K:\video engine` 被判"缺少审计脚本" | 适配层写死上游 `_build/` 布局 | `EnginePaths.layout` 探测两种布局；命令/工作目录按布局切换 |
| 阶段 Agent 一个 pv 工具都看不见 | `is_configured()` 只读环境变量 | 改为读插件配置；`doctor` 不做门控（诊断价值） |
| 审计只有字符串 | stdout 直接透传 | `parse_audit()` 结构化；`verdict` 区分 `pass_with_warnings` |

---

## 七、测试

```powershell
# 单测（假引擎 + 一个真引擎集成用例）
python -m pytest tests/clipwright/test_pv_engine_plugin.py -q

# 真引擎（设了环境变量才会跑集成用例）
$env:CLIPWRIGHT_PV_ENGINE_DIR = "K:\video engine"
python -m pytest tests/clipwright/test_pv_engine_plugin.py -q
```
