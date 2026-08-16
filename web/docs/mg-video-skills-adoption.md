# MG 动画生成技能采纳说明 (video-skills adoption)

- **日期**: 2026-08-05
- **上下文**: Phase 5 — ClipWright MG 流水线（内置 `llm_mg` 引擎 + 8 个固定模板降级）。
- **背景**: T11 已在渲染阶段加了占位符双保险（生成器 union 扫描 ∪ 渲染器综合填充 ∪
  渲染后残留扫描 → fallback），使非规范占位符不再泄漏进最终 HTML。但**根因在生成阶段**：
  LLM 提示词没有强制设计令牌纪律（配色/字体锁定自 Persona）、没有镜头卡式结构化 MG
  scene schema、还允许临时发明占位符名。本任务升级提示词（后端 `config.yaml`），
  从源头减少这类问题，与 T11 的渲染阶段兜底互补。

## 采纳的模式（Adopted patterns）

1. **设计令牌纪律 (design token discipline)**
   配色与字体从「当前创作者（Persona）视觉风格」锁定，全片最多 3-5 个调色板锚点，
   反复呼应；禁止发明调色板之外的颜色，`style.font_family` 不得指定与 Persona 无关字体。
   - 落地：`clipwright/animation/mg/config.yaml` 新增「## 设计令牌（Design Tokens）」段落
     （`{accent}` 优先，必要时声明 `{primary}`/`{secondary}` 进 params）。
   - 数据源：`_build_context_section`（`clipwright/animation/mg/generator.py`）注入的
     primary_color / secondary_color / accent_color / text_color / font。

2. **镜头卡式结构化 MG scene (shot-card schema)**
   每个生成的 MG 是一张「镜头卡」，JSON 顶层给出镜头意图，元素必须落实：
   `shot_type`（title_reveal / data_impact / comparison / flow / quote / mindmap）、
   `duration_s`（= duration_sec）、`energy`（low/medium/high，由 pacing 驱动）、
   `animation`（一句话意图）、`colors`（本镜头调色板锚点）、`text_blocks`
   （`|` 分隔文本段按序映射占位符）。
   - 落地：`config.yaml` 新增「## 镜头卡（Shot Card）」段落；参考示例 1 补充
     `shot_type: data_impact` / `energy: high` / `colors` / `text_blocks` 字段。

3. **模板替换纪律 (canonical placeholder set + declare-in-params)**
   只允许规范占位符集 `{text} {value} {unit} {subtitle} {accent}` 与 params 中显式声明的
   参数键；禁止临时发明 `{left_label}` / `{mirror_label}` 之类未声明键；确需自定义参数时
   先在 `params` 中声明（`{"key": {"type": "string", "default": ""}}`）再在元素中使用。
   - 落地：`config.yaml` 硬性约束新增「占位符纪律」「声明即用（declare-in-params）」两条。
   - 运行时兜底：`_build_llm_params` 的 union 扫描（`generator.py`）+ `_handle_mg_animation`
     的 param-key 对齐（`clipwright/agents/animation_agent.py`）按声明键按位置填充。

4. **占位符双保险 (placeholder double-protection)**
   生成器 union 扫描 ∪ 渲染器综合填充 ∪ 渲染后残留扫描 → fallback，保证 `{key}` 字面量
   不原样渲染进最终 HTML。属 T11 既有实现，本轮提示词纪律（第 3 条）进一步降低其触发率。
   - 落地：`_build_llm_params` union 扫描（`generator.py:643`）、
     `_render_html_no_residuals` 残留二次填充（`generator.py:693`）、
     `_RESIDUAL_PLACEHOLDER_RE`（`generator.py:23`）。

5. **回退 plan (fallback template matching)**
   LLM 失败 / 校验不通过 / 残留占位符时，按描述语义匹配既有模板并填满参数，保证降级可渲染。
   - 落地：`FallbackEngine.find_best_template` + `fill_template_params`
     （`clipwright/animation/mg/fallback.py`）+ `_fallback_generate`（`generator.py`）。

## 来源 (Source repos)

- https://github.com/Vincentwei1021/video-shotcraft （SKILL.md：设计令牌 / 镜头卡 / 模板替换纪律）
- https://github.com/taylorzhou16/video-gen-en （storyboard-spec / fallback_plan）
- https://github.com/Nobulax/promptloom （占位符预检）
- https://github.com/iart-ai/motion-skills
- https://github.com/openai/skills （sora）

## 未采纳（Not adopted）

- **Remotion 运行时**：不引入。保持 hyperframes HTML→MOV 链路，零新增运行时依赖，
  Canvas/CSS 渲染面已覆盖当前需求，引入新运行时收益不抵成本。
- **模板目录扩容**：维持 8 个固定模板。扩展走 LLM 动态生成（`llm_mg`），
  靠提示词纪律约束质量，而不是扩库增加维护面。

## 验证（Verification）

- `config.yaml` 仍为合法 YAML，`prompt.system_template` 内嵌 2 个 few-shot 示例均
  可解析且通过 `validate_mg_json`。
- `tests/clipwright/test_llm_mg_fallback.py`、`test_mg_generator_critique.py`、
  `test_mg_config.py`、`test_llm_mg_validator.py` 全部通过（75 passed）。
