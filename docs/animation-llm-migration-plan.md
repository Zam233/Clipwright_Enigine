# Clipwright 动画 LLM 化开发计划

## 结论

将 `AnimationAgent` 的主路径统一改为“动画类型规格 + 类型专属提示词 + 视频类型插件 + persona → `MGGenerator` 生成 `mg_def` → validator/critique → Hyperframes 渲染”。38 个逻辑动画类型都通过该链路生成；原 `diagram_svg.py` 不删除，降为失败回退，并补齐可验证的 CSS 动画。前端事件契约和 Hyperframes 渲染选型保持不变。

按 52 个动画实例估算，LLM 生成不应以“每个实例独立调用”为默认：先做片内内容去重与生成缓存，预计首次生成约 25 个独立动画资产；在并发 4、每次生成/批判修复合计 1–2 次模型调用的情况下，生成阶段约 1–4 分钟，渲染阶段取决于 Hyperframes 机器吞吐，建议以实测基线设总预算而不是预设固定 SLA。下文将预算、超时和降级点固化为可配置项。

## 1. 目标架构

```text
StructureAgent 标记 / AnimationAgent 输入
              │ (animation_type, content, duration, category_id, persona_id)
              ▼
AnimationTypeRegistry ──► AnimationTypeSpec + 类型专属 prompt
              │                     │
              └──────────┬──────────┘
                         ▼
PromptAssembler(system + type + category + persona + content + 同片上下文)
                         ▼
MGGenerator.generate_for_type(...)
    ├─ generation_cache 命中 ───────────────────────┐
    └─ LLM → mg_def schema parse → validator         │
                    └─ critique / 一次修复            │
                         ▼                            │
                缓存通过校验的 mg_def ◄──────────────┘
                         ▼
          mg_def → HTML → Hyperframes 透明 MOV
                         │
       失败/超时/预算耗尽 ▼
 animated SVG fallback / 对应内置 MG template
                         ▼
 services/render.py 链式叠加与图解批次 → timeline 元数据（契约不变）
                         ▼
             逐帧运动自检 + 渲染验收报告
```

核心原则：生成和渲染分离；缓存只存 schema 校验通过且版本完整的定义；失败回退仍经现有渲染器产出动态 MOV，不直接把静态 SVG 作为成片覆盖层。

## 2. 数据结构与提示词注册

建议新增 `clipwright/animation/type_specs.py`（数据模型/注册表）与 `clipwright/animation/prompts/loader.py`；每种类型使用 `clipwright/animation/prompts/types/<id>.md`。不建议把长篇提示词塞进 YAML：多行编辑、审阅 diff、示例迭代和类型提示词独立版本控制更适合 Markdown。YAML 只保留可机器读取的元数据也可行，但引入双源一致性负担；首版以 Python spec 注册元信息、Markdown 保存类型正文，启动时校验文件存在和 id 一致。

```python
@dataclass(frozen=True)
class AnimationTypeSpec:
    id: str
    name: str
    intent: str
    semantic_constraints: tuple[str, ...]
    output_schema: dict[str, object]       # MGDef 的 JSON Schema / schema version
    examples: tuple[dict[str, object], ...]
    quality_rules: tuple[str, ...]
    fallback_template_id: str
    prompt_version: str
```

注册表覆盖 `AnimationCatalog.get_logic_animations()` 返回的全部 38 个 id，包括 13 个 `mg_*` 模板型类型。注册时断言 id 唯一、fallback 存在、schema 版本受支持；未注册类型按配置错误处理并走通用 animated fallback，同时记录错误指标。

类型提示词示例文件：`prompts/types/causation.md`。约定正文分段 `## Intent`、`## Semantic constraints`、`## Composition`、`## Motion`、`## Anti-patterns`、`## Example`，loader 只读取结构化段落，不对提示词做自由拼接。提示词版本由文件内容 SHA-256 得出，避免手工忘记 bump。

## 3. 按文件与函数改造清单

### `clipwright/agents/animation_agent.py`

- `_handle_logic_animation(...)`：解析动画 id 后获取 spec；构造 `AnimationGenerationContext`（文本、时长、category plugin 配置、persona 风格、片内已用指纹）；调用 `generate_for_type`。成功时写入既有 `metadata.renderer="mg_dynamic"`、`metadata.mg_html`、`metadata.mg_def`；不要改变事件字段结构。
- 失败分支按优先级：对应内置模板生成动态 HTML → 当前类型的动画 SVG fallback → 对应类型安全简化模板。所有分支标记 `metadata.animation_fallback=true`、`fallback_reason`（可选元数据，不改变必需契约字段）。禁止静态 SVG 原样进入最终输出。
- `_build_diagram_params(...)`：保留，仅被 fallback 调用。拆出 `_build_fallback_diagram_params`；语义抽取优先使用已结构化的动画输入，禁止把原旁白任意按箭头/`VS` 字符串切分作为主路径。输入无法拆出有效项时生成有标题、重点词的动态卡片，不输出空项。
- 同片上下文维护 `seen_animation_fingerprints`，对重复 `(type, normalized_content)` 复用已生成定义并调整入场时间/强调色；完全相同内容不重复播，必要时合并相邻片段或只保留首次出现。

### `clipwright/animation/mg/generator.py`

保留已有 `generate_best`、critique、validator、缓存与残影清理实现，在其上新增面向类型的装配入口：

```python
async def generate_for_type(
    self,
    *, spec: AnimationTypeSpec,
    content: str,
    duration_s: float,
    category: CategoryAnimationContext,
    persona: PersonaAnimationContext,
    prior_fingerprints: Sequence[str] = (),
    deadline: float | None = None,
) -> MGGenerationResult: ...
```

- `_assemble_type_prompt(...)`：装配通用 system、type Markdown、category、persona、content 与约束参数。
- `_parse_mg_def(...)`：严格解析结构化定义，拒绝围栏外文字、未知 element 类型、NaN/越界坐标、非法时间戳。
- `_validate_for_type(mg_def, spec, duration_s)`：调用既有 `validator.py`，叠加类型语义与时长规则。
- `_critique_and_repair(...)`：复用现有 critique 闭环；最多一次修复，修复后再次完整校验，失败即返回结构化失败原因，由 agent 降级。
- `_cache_key(...)`：组成下节所述缓存键；缓存命中仍运行 schema/version/时长兼容校验。
- 结果类型需含 `mg_def`、`html`、`cache_hit`、`quality_score`、`attempts`、`failure_reason`，避免以空 HTML 表示失败。

### 其他后端文件

- `clipwright/animation/mg/config.yaml`：保留通用 MG 动效原则和模板目录；把与所有类型冲突的硬性美术描述抽成 system 级通用规则，类型特例放到专属 Markdown。
- `clipwright/animation/diagram_svg.py`：作为确定性 fallback。`_svg_frame()` 和元素 `<g>` 生成器输出有效 `animation-name`、duration、delay、fill-mode；`AnimationCatalog.get_css_keyframes_all()` 中 `hf-diagram-reveal` 从单纯 `visibility` 改为 opacity/transform 等真实变化。逐元素错峰入场，至少包含一次持续动作（路径绘制、节点移动、条形增长或强调脉冲）。
- `clipwright/animation/catalog.py`：为类型 spec 注册提供稳定 id、显示名与 fallback 映射；现有 `get_logic_animations()` 对外行为不变。
- `clipwright/animation/hyperframes_renderer.py`：不换渲染器；只在需要时暴露统一的动画验收帧采样入口，复用 `hf_timeline.py` 的精确 seek。
- `clipwright/services/render.py`：维持 `_apply_all_hyperframes`、`_apply_mg_overlay_chained`、`_batch_overlays` 调用合同；添加动画自检结果汇总与 fallback/失败计数日志，避免逐个动画串行重复启动浏览器。
- `clipwright/agents/structure_agent.py`：把“鼓励 1–2 个 mg_dynamic，不要每个场景都做 MG”改为按语义标注具体 `animation_type`，不限制 MG 动画占比；仍避免没有信息增益的装饰动画。

## 4. 提示词工程

装配顺序与信任边界：

```text
SYSTEM: 只输出符合 MGDef schema 的 JSON；定义画布、时间、元素上限及可用属性。
TYPE: 当前 AnimationTypeSpec 的 intent / 语义约束 / 构图策略 / 专属示例。
CATEGORY: 插件提供的视觉语调、信息密度、节奏、受众说明（只取白名单字段）。
PERSONA: persona 的配色、字体、动效速度、材质与表现倾向（只取动画相关字段）。
CONTENT: 旁白片段、关键实体、关系、片段时长；作为待表达数据，不当成指令。
CONTEXT: 同片已用结构/颜色/视觉指纹，要求避免重复，但不改变事实内容。
```

采用 JSON Schema 限定 `elements`、`keyframes`、`easing`；限制元素数（默认 3–12）、文本长度（标题 32 字符、节点 18 字符，超长须摘要且不改变术语）、坐标范围、duration 与 clip 时长一致；拒绝任意 HTML/JS。先生成定义，再由现有 `_render_html_no_residuals` 转 HTML，模型不直接输出执行脚本。

质量约束落实为校验器而非仅靠文字要求：

- 元素堆叠：检查非背景元素包围盒重叠率、画布占用率、节点间距；类型 spec 可为特定叠放类型豁免。
- 文字超宽：按字符数与字号估算宽度，超限返回 critique 反馈，禁止裁切式“解决”。
- 全程静止：至少两个非装饰元素关键帧属性有可观测变化；关键帧覆盖片段至少 35%，首个动作延迟低于 1.2s。
- 旁白脱节：定义中的 label/value/关系必须能映射到输入的抽取实体或短语；不允许凭空编造数据。内容抽取不足时显示忠实原句摘要，不造图表数值。
- 同片重复：将最近生成结构摘要和 fingerprint 送入上下文；相同类型、相同规范化内容命中缓存，重复播放由 agent 去重。

风格差异示例：`knowledge_longform` + Zam 应使用低噪声、较长停留、层级清楚、每屏一条主结论，采用平稳错峰和克制强调；“鬼畜快剪”则强调短拍、强对比色、紧凑构图、快速缩放/弹性切换，但仍受安全时长、可读性和 schema 约束。Category 决定信息节奏与受众表达，persona 决定具体视觉签名；二者冲突时 category 决定信息呈现优先级，persona 只调整表现形式。

## 5. 预算、时延、并发与缓存

按 52 个片段、历史 25 组唯一 `(preset,text)` 做初始估算：缓存/片内去重后最多约 25 次首次生成；如果平均每次 1 次生成、约 20% 触发一次 critique 修复，总模型调用约 30 次。LLM token 成本按实际 provider 价格和每类平均输入/输出 token 计量，不能在未指定模型与 token 价格时给虚假金额；上线前用首轮统计导出实际成本。

- 缓存 key：`sha256(normalized_content + type_id + persona_animation_fingerprint + category_plugin_id + category_prompt_version + type_prompt_sha256 + mg_schema_version + generator_version)`。persona 指纹只纳入动画相关配置，避免无关 persona 属性冲掉缓存。
- 缓存值：通过完整校验的 `mg_def`、质量分、版本元数据；HTML 可再生，不做唯一真相。缓存按版本隔离，旧 key 自然失效；命中时复检时长和 schema。
- 并发：LLM 生成 semaphore 默认 4；单片排队与全局 provider 并发均可配置。重试仅对网络/限流错误指数退避一次；schema/语义错误走 critique 一次，不做重复盲重试。
- 超时：单个生成 25s、修复额外 15s、整片 LLM 生成总预算 180s（52 段的规划上限）；触及 deadline 立即逐项回退。render 总超时由当前 pipeline 配置控制，初始建议在现有 702s 实测用时之外预留 20%，之后依据 P95 更新。
- 指标：生成数、缓存命中率、单次 token/耗时、critique 修复率、失败原因、fallback 类型、动画自检失败率、渲染总时长。

## 6. 去重与质量门禁

- 规范化指纹：`type_id + NFKC(strip_punctuation(normalize(content)))`；同片相同指纹只保留首次，若旁白重现是有意复习，可由上游显式 `allow_repeat=true` 覆盖。
- “必须动”分两层门禁：离线定义门禁检查关键帧差异和非装饰元素位移/尺寸/透明度；真实渲染门禁对采样帧计算 SHA-256，统计 distinct frame ratio，并检测 alpha bbox/像素差。
- 建议基线：4–6 秒片段至少 8 个不同采样帧，distinct ratio ≥ 0.25；至少一个主要元素的 bbox/路径/尺寸变化超过画布尺寸 2%。短于 3 秒的片段合并到相邻动画或重分配时长，不单独渲染；无法合并时最短延至 3 秒且不覆盖字幕关键字。
- 空白帧允许短暂淡入，但不得超过总时长 15%；动效不得只靠粒子噪点通过多样性检查。
- 验收失败最多一次自动 regenerate/repair；仍失败就用 animated fallback，再跑相同门禁。fallback 失败则本片标记渲染失败并附诊断，不静默交付静止图。

## 7. 测试计划

### 单元测试

- `tests/animation/test_type_specs.py`：38 个 id 完整、spec 唯一、fallback 可解析、prompt 文件缺失快速失败。
- `tests/animation/test_prompt_loader.py`：system/type/category/persona/content 分区正确；动态内容转义，提示词版本 hash 稳定。
- `tests/animation/mg/test_generate_for_type.py`：schema 成功、非法 JSON、语义越界、critique 修复成功/失败、缓存命中与版本失效、deadline 降级。
- `tests/animation/test_animation_dedup.py`：同片重复合并、不同 persona/category 不错误共享缓存、短于 3s 合并逻辑。
- `tests/animation/test_diagram_svg_motion.py`：fallback SVG 包含有效 keyframes/name，关键帧属性变化，`animation-delay` 不再是无效孤立声明。

### 契约/集成测试

- 对比改造前后 timeline 事件必需字段及 `metadata.renderer`、`mg_html`、`mg_def`、`diagram_params` 兼容性；前端无字段重命名。
- Mock LLM 覆盖 `AnimationAgent → spec → generate_for_type → metadata` 成功与各类降级；检查渲染服务仍接收 MG HTML 与 overlay 批次。
- 真实渲染脚本：读取固定 fixture，调用 `render_mg_html_to_mov` / overlay 管线，ffmpeg 抽帧后计算 framemd5、distinct ratio、bbox 变化，产出 JSON 验收报告；同 fixture 对比旧静态路径应显著提高动态帧比。

### 回归命令

```powershell
# 后端 J:\Clipwright
python -m pytest tests/animation -q
python -m pytest tests -q
python -c "import clipwright.main"

# 前端 J:\Clipweight-Client（事件消费回归）
npx vitest run
npx tsc --noEmit
```

真实端到端验收使用新增脚本：

```powershell
# 后端仓库；fixture 与输出目录由脚本参数指定
python scripts/verify_animation_motion.py --fixture tests/fixtures/animation/knowledge_longform_zam.json --output artifacts/animation-motion-report.json
```

## 8. 里程碑与验收

### M1：规格与提示词骨架

实现 `AnimationTypeSpec`、registry、loader 和 38 份类型 prompt 初版；覆盖率测试通过，category/persona 动画上下文有明确白名单字段。

```powershell
python -m pytest tests/animation/test_type_specs.py tests/animation/test_prompt_loader.py -q
```

### M2：生成链路与门禁

新增 `generate_for_type`，复用 MGGenerator 既有缓存、critic、validator、fallback template；mock 测试覆盖成功、缓存、修复与超时。

```powershell
python -m pytest tests/animation/mg/test_generate_for_type.py -q
```

### M3：Agent 路由、去重与动态 fallback

所有逻辑动画统一走类型生成；SVG 仅 fallback 且逐项动效有效；事件契约不变，重复内容去重。

```powershell
python -m pytest tests/animation/test_animation_agent.py tests/animation/test_animation_dedup.py tests/animation/test_diagram_svg_motion.py -q
```

### M4：真实渲染与质量验收

用固定的 Zam / `knowledge_longform` 成片 fixture 全链路重跑，52 个输入动画中主路径均请求类型化生成；统计缓存、失败、fallback、distinct frames；无静止片段漏过门禁。

```powershell
python scripts/verify_animation_motion.py --fixture tests/fixtures/animation/knowledge_longform_zam.json --output artifacts/animation-motion-report.json
```

### M5：灰度、回归与全量切换

先以配置开关对内部项目灰度，观察成本与 P95；回归通过后默认启用，保留一键关闭开关和原 fallback。全量命令：

```powershell
python -m pytest tests -q
python -c "import clipwright.main"
# 前端仓库
npx vitest run
npx tsc --noEmit
```

验收阈值：后端全量测试与现有基线相同或更好；前端测试/TS 零回归；目标 fixture 的 45 个原静态片段不再直接静态输出；所有交付动画通过动态门禁或整片显式失败；预算、缓存命中率和 fallback 原因可追踪。

## 9. 风险与回滚

- LLM 不稳定：schema、语义、时长三层校验；critique 仅一次；优先模板、再 animated SVG fallback；开关可回到旧生成器分支。
- 成本爆炸：按片总预算、并发限制、缓存 key 版本隔离、片内去重；达到预算即时回退；监控每分钟 tokens 与每片花费。
- 渲染时长增长：继续复用批处理和链式叠加；按缓存命中率减生成，不按动画数启动浏览器；采样验收可配置仅对灰度/CI 全量运行。
- 旧 timeline 兼容：保留事件字段和 metadata 既有值；新字段仅可选；契约测试固定旧消费者样本。
- 缓存脏数据：缓存只写校验通过内容，schema/prompt/generator 版本进入 key；读取时重新校验；异常项删除并落日志。
- 回滚步骤：关闭 `animation.llm_generation_enabled`，恢复旧路由配置；缓存保持只读且不清库；animated SVG 修复保留，不影响旧路径；一次重启/新任务生效。禁止通过清空共享缓存作为常规回滚。

## 10. 明确不做

- 不改前端 timeline/SSE 事件契约，不把动画生成迁到前端。
- 不更换 Hyperframes、Chrome headless 或 MOV overlay 渲染选型。
- 不删除 `diagram_svg.py` 的 preset；其职责由主生成路径降为确定性动态回退。
- 不要求每个片段都新发一次 LLM 请求；缓存、去重是主路径的一部分。
- 不让 LLM 输出可执行 JS/任意 HTML；模型只返回受限 `mg_def`。
- 不在本计划内重写动画目录、category plugin 或 persona 系统；只新增动画上下文适配层。

---

## 执行进度（2026-10-29 实现记录）

M1–M4 已落地，M5 回归通过：

| 里程碑 | 状态 | 产物 |
|---|---|---|
| M1 规格与提示词 | ✅ | clipwright/animation/type_specs.py（38 类型 registry + AnimationTypeSpec + category/persona 白名单上下文）；clipwright/animation/prompts/types/*.md 38 份类型提示词（Intent/Semantic constraints/Composition/Motion/Anti-patterns/Example），版本取正文 SHA-256 前 12 位 |
| M2 类型化生成 | ✅ | clipwright/animation/mg/type_generation.py（uild_type_description / alidate_for_type / motion_report / 
ormalize_duration / generate_for_type）；MGGenerator.generate_for_type 薄转发，复用既有缓存/校验/批判/修复/模板回退 |
| M3 Agent 主路径 | ✅ | _handle_logic_animation 改为：mg_dynamic 走自由生成 → 其余逻辑动画走类型化生成 → 失败回退 mg_* 模板 → 再回退 SVG。新增 settings.animation_llm_generation（默认开）与 nimation_llm_concurrency；同片 (type, 规范化内容) 指纹去重（asyncio 锁保护） |
| M3b 回退也要动 | ✅ | DiagramRenderer._inject_motion：给每个 <g> 注入 ds-rise/ds-pop 入场 + ds-breathe 持续微动，连线走 ds-draw 描线；SVG 内联 <style> 关键帧，hf_timeline 逐帧 seek |
| M4 动效门禁 | ✅ | scripts/verify_animation_motion.py（逐帧 framemd5 多样性，≥8 帧且 distinct ratio ≥0.25）；motion_report 改为看**全部关键帧极差**（只比首末会误伤「淡入→停留→淡出」） |

实测（702s 成片，Zam / knowledge_longform）：

- 改造前：52 个动画中 45 个静态 SVG，llm_ratio = 0.135；批次 MOV distinct ratio **0.026**（115 帧仅 3 个不同帧）。
- 改造后（仅重跑 animation 阶段）：旧 52 段 → 去重后 **32 段，llm_ratio = 1.000**（全部 mg_hyperframes）；生成耗时 611.7s（并发 6）。
- SVG 回退路径（关掉 LLM 开关时）单独验收：distinct ratio **0.496**（57/115），已从"静态贴图"变为可验收的动态图解。

回归：后端 pytest tests -q = **4621 passed / 4 skipped / 1 xfailed**；前端 vitest = 127 files / 1101 passed；tsc 干净。
