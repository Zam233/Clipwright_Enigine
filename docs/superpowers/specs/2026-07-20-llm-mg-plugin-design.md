# LLM 动态 MG 动画插件 — 设计规格

> 日期: 2026-07-20 | 状态: Draft | 作者: Shadow Monarch

## 一、目标

创建一个新插件 `llm_mg`，使 AnimationAgent 可以将 MG 动画需求输出给插件，由 LLM 动态生成完整的 MG JSON 定义（elements + keyframes + params），然后走现有 MGRenderer → Hyperframes 渲染管线。同时修复现存的 `mg_animations` 伪插件问题。

## 二、现状分析

### 2.1 现有架构

```
User Input
    ↓
RequirementsAgent ──→ creative_brief (dict)
    ↓
StructureAgent ──→ scenes[] (带 description 含动画标记)
    ↓
EditAgent ──→ Timeline (粗剪)
    ↓
AnimationAgent ──→ 解析标记 → 三种路由:
    ├── [文字动画]xxx  → drawtext keyframes
    ├── [逻辑动画]xxx  → DiagramSVG / Hyperframes
    └── [逻辑动画]mg_* → MGRenderer.render(mg_def, params) → HTML → Hyperframes
```

### 2.2 mg_animations 伪插件问题

| 文件 | 现状 | 问题 |
|------|------|------|
| `plugin.yaml` | ❌ 缺失 | PluginLoader.discover() 会跳过此目录 |
| `main.py` | ❌ 缺失 | 无 BasePlugin 子类，无法加载 |
| `__init__.py` | ❌ 缺失 | 无 Python 包结构 |
| JSON 模板 | ✅ 5 个 | 通过硬编码路径读取，绕过插件系统 |

`MGRenderer.load_animation()` 硬编码路径:
```python
# mg_renderer.py:237 — 绕过插件系统
base = Path(__file__).resolve().parent.parent.parent / "plugins" / "mg_animations" / "animations"
```

### 2.3 现有 5 个 MG 模板

| 模板 ID | 名称 | 元素数 | 参数 |
|---------|------|--------|------|
| `mg_title_reveal` | 标题揭示 | 2 (text+shape) | text, accent |
| `mg_progress_bar` | 进度条 | 3 (text+shape×2) | text, value, unit |
| `mg_counter_up` | 数字滚动 | 2 (text×2) | text, value |
| `mg_comparison_split` | 左右对比 | 5 (shape×2+text×3) | text |
| `mg_callout_badge` | 标签徽章 | 3 (shape+text×2) | text, subtitle |

## 三、设计方案

### 3.1 整体架构

```
User: "产品A和B对比，A在性能上胜出"
    ↓
RequirementsAgent ──→ creative_brief + animation_intent
    ↓
StructureAgent ──→ scenes[] 带 [逻辑动画]mg_dynamic:{intent_json}
    ↓
AnimationAgent ──→ _handle_logic_animation() 检测 mg_dynamic
    ↓
┌─ llm_mg Plugin ────────────────────────────────────┐
│                                                     │
│  LLM.generate_mg_json(requirements, persona_style)  │
│     ↓                                               │
│  ① LLM 尝试生成完整 MG JSON                          │
│     ├── 成功 → MGRenderer.render(json, params)      │
│     └── 失败 → ② 模板降级（匹配已有模板 + 参数填充）  │
│                                                     │
│  输出: HTML 字符串 → clip.metadata.mg_html           │
└─────────────────────────────────────────────────────┘
    ↓
AnimationAgent ← Clip(kind=ANIMATION, metadata={mg_html, ...})
    ↓
RenderService → Hyperframes → MOV overlay → final video
```

### 3.2 插件结构

```
plugins/llm_mg/
├── plugin.yaml            # 插件清单
├── config.yaml            # LLM prompt 模板 + 生成配置
├── main.py                # LLMMGPlugin(CapabilityPlugin) 主类
├── __init__.py            # 包标记
├── generator.py           # LLM MG JSON 生成器
├── validator.py           # MG JSON Schema 验证器
├── fallback.py            # 降级策略（语义→模板匹配）
└── templates/             # 迁移自 plugins/mg_animations/animations/
    ├── mg_title_reveal.json
    ├── mg_progress_bar.json
    ├── mg_counter_up.json
    ├── mg_comparison_split.json
    └── mg_callout_badge.json
```

### 3.3 plugin.yaml

```yaml
id: "llm_mg"
name: "LLM Motion Graphics Generator"
version: "1.0.0"
kind: "capability"
description: "LLM 驱动的动态 MG 动画生成插件 — 从自然语言需求生成 HTML/CSS 动画"
author: "Clipwright"
entry_point: "llm_mg.main"
```

### 3.4 核心数据流

#### RequirementsAgent → 新增 animation_intent

在 `CREATIVE_BRIEF_SYSTEM` prompt 中增加动画需求识别指令:

```
## 动画需求识别
如果用户的创作需求中提到了视觉效果、数据展示、对比、流程等，
在 brief_draft 中设置 animation_intents 数组，每个元素描述一个场景的动画需求。
animation_intents 格式:
[
  {
    "scene_index": null,  // 场景索引（如已确定分镜则填写，否则 null）
    "type": "mg",         // mg（动态图形）/ text（文字动画）/ logic（逻辑图解）
    "description": "自然语言描述该动画应呈现的效果",
    "text_content": "动画中要显示的文字内容",
    "style_hint": "风格提示: tech_dark / minimal_clean / bold_vibrant / retro",
    "suggested_template": "建议使用的已有模板 ID，如不确定则留空"
  }
]
只在用户明确需要视觉动画时填写此字段，不要滥用。
```

`creative_brief` 中新增字段:
```json
{
  "animation_intents": [
    {
      "scene_index": 2,
      "type": "mg",
      "description": "产品性能参数对比",
      "text_content": "骁龙8Gen3|天玑9300|骁龙胜出",
      "style_hint": "tech_dark",
      "suggested_template": "mg_comparison_split"
    }
  ]
}
```

#### StructureAgent → 新标记格式

现有标记: `[逻辑动画]comparison:A vs B → A胜出`
新增标记: `[逻辑动画]mg_dynamic:{"description":"产品对比","text":"A|B|A胜出","style":"tech"}`

#### AnimationAgent → 新路由

在 `_handle_logic_animation()` 中增加:
```python
if anim_id == "mg_dynamic" or anim_id.startswith("mg_dynamic"):
    await self._handle_llm_mg(anim_track, vid_clip, marker, persona_style)
    return
```

### 3.5 插件核心接口

```python
class LLMMGPlugin(CapabilityPlugin):
    """LLM 驱动的 MG 动画生成插件。"""

    async def generate_mg(
        self,
        description: str,          # 动画需求描述
        text_content: str,         # 动画中的文本内容
        persona_style: dict,       # Persona visual_config
        scene_context: dict | None,  # 当前场景上下文 {title, keywords, prev_scene, next_scene}
    ) -> dict:
        """
        Returns:
            {
                "success": bool,
                "html": str,           # 生成的 HTML（成功时）
                "mg_def": dict,        # MG JSON 定义（含 animation_id）
                "method": str,         # "llm" | "fallback" | "cached"
                "fallback_template": str | None,  # 降级时使用的模板 ID
                "generation_id": str,  # 唯一 ID，用于后续保存引用
            }
        """

    def save_as_template(self, generation_id: str, custom_name: str = "") -> str:
        """将某次生成的 MG JSON 保存为可复用模板。
        
        Args:
            generation_id: generate_mg() 返回的 generation_id
            custom_name: 自定义模板名称（为空则自动生成）
        
        Returns:
            str: 保存后的模板文件路径
        """
```

**保存功能实现方式:**
- 生成的 `mg_def` 持久化在 `PluginData/plugins/llm_mg/generations/{generation_id}.json`
- 用户通过 API `POST /api/plugin/llm_mg/save-template` 或前端按钮触发
- 保存后移动到 `templates/` 目录，`animation_id` 自动分配（避免冲突）
- 前端编辑器在动画 clip 上展示"保存为模板"按钮（后续 Phase 实现）
```

### 3.6 LLM Prompt 设计

```
System: 你是一个 MG 动画生成器。根据用户需求生成符合规范的 MG 动画 JSON。

## MG JSON Schema
{...完整 schema + 示例...}

## 可用动画属性
- opacity (0~1): 透明度
- scale: 缩放比例
- translate_x / translate_y: 位移 (px)
- rotate: 旋转角度 (deg)
- width: 宽度 (px, shape 元素)

## 可用元素类型
- type: "text" — 文字元素 (content, font_size, font_color, font_weight)
- type: "shape" — 形状元素 (shape: rect|ellipse, color, width, height, border_radius)

## 约束
- 每个动画至少 2 个 keyframes
- time 从 0 开始
- 最后一个 keyframe 的 time 不超过 duration_sec
- 所有坐标以 1920x1080 为基准

User: 产品 A 和 B 对比，A 在性能上优于 B。A 用蓝色，B 用红色。

Output: {完整 MG JSON}
```

### 3.7 降级策略

| 失败场景 | 降级行为 |
|---------|---------|
| 插件未加载 (llm_mg not found) | AnimationAgent 检测到 mg_dynamic 标记但插件不可用 → 降级为纯文字 clip (drawtext)，通过 trace event 警告用户 |
| LLM 返回非 JSON | 从文本中提取 key=value，匹配最接近的已有模板 |
| JSON schema 校验失败 | 尝试修复常见错误（缺少 duration_sec、无效 keyframes），仍失败则模板降级 |
| LLM 完全不可用 | 直接使用 mg_comparison_split 模板 + 原始 text_content |
| 无匹配模板 | 生成纯文字 clip（drawtext 降级） |

插件不可用时的 AnimationAgent 行为:
```python
async def _handle_llm_mg(self, ...):
    plugin = PluginLoader.get("llm_mg")
    if plugin is None:
        logger.warning("llm_mg 插件未加载，mg_dynamic 降级为 drawtext")
        add_event(..., "warning", "LLM MG 插件未加载，动画降级为文字显示")
        self._create_fallback_text_clip(anim_track, vid_clip, text_content)
        return
    # ... 正常流程
```

### 3.8 mg_animations 修复计划

1. 将 5 个 JSON 文件迁移到 `plugins/llm_mg/templates/`
2. 更新 `MGRenderer.load_animation()` 的模板搜索路径，**同时**支持:
   - `plugins/llm_mg/templates/` (新的正确路径)
   - `plugins/mg_animations/animations/` (向后兼容，标记 deprecated)
3. 在 `llm_mg/main.py` 的 `initialize()` 中将 5 个模板注册到 `AnimationCatalog`

### 3.9 渲染流程

MG 动画渲染流程不变，复用现有管线:

```
MG JSON → MGRenderer.render() → HTML string
  → 存入 clip.metadata.mg_html
  → RenderService 检测 mg_html → HyperframesRenderer
  → npx hyperframes render → MOV (透明)
  → ffmpeg overlay → 最终视频
```

## 四、Schema 变更

### 4.1 新增 AnimationIntent

```python
# clipwright/schema/agent.py
class AnimationIntent(BaseModel):
    """动画需求意图 — RequirementsAgent → StructureAgent → AnimationAgent。"""
    scene_index: int = Field(description="目标场景索引")
    type: str = Field(default="mg", description="动画类型: mg | text | logic")
    description: str = Field(description="自然语言动画需求描述")
    text_content: str = Field(default="", description="动画中的文本")
    style_hint: str = Field(default="", description="风格提示")
    suggested_template: str = Field(default="", description="建议的模板 ID")
```

### 4.2 RequirementsOutput 扩展

```python
class RequirementsOutput(BaseModel):
    # ... 现有字段 ...
    animation_intents: list[AnimationIntent] = Field(
        default_factory=list,
        description="LLM 识别的动画需求意图"
    )
```

### 4.3 AnimationOutput 扩展

```python
class AnimationOutput(BaseModel):
    # ... 现有字段 ...
    generated_mg_count: int = Field(default=0, description="LLM 生成的 MG 动画数")
```

## 五、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `plugins/llm_mg/plugin.yaml` | 新建 | 插件清单 |
| `plugins/llm_mg/__init__.py` | 新建 | 包标记 |
| `plugins/llm_mg/main.py` | 新建 | 插件主类 (LLMMGPlugin) |
| `plugins/llm_mg/generator.py` | 新建 | LLM MG JSON 生成器 |
| `plugins/llm_mg/validator.py` | 新建 | Schema 验证器 |
| `plugins/llm_mg/fallback.py` | 新建 | 降级策略（语义→模板匹配） |
| `plugins/llm_mg/config.yaml` | 新建 | LLM prompt 配置 |
| `plugins/llm_mg/templates/*.json` | 迁移 | 5 个 MG JSON 模板 (从 mg_animations) |
| `plugins/llm_mg/storage.py` | 新建 | 生成结果持久化 (generations) + 保存为模板 |
| `plugins/mg_animations/` | 删除 | 伪插件，内容已迁移 |
| `clipwright/animation/mg_renderer.py` | 修改 | 搜索路径增加 `llm_mg/templates/`，兼容旧路径 |
| `clipwright/agents/animation_agent.py` | 修改 | 增加 `mg_dynamic` 路由 + `_handle_llm_mg()` + 插件不可用降级 |
| `clipwright/agents/requirements_agent.py` | 修改 | CREATIVE_BRIEF_SYSTEM prompt 增加 animation_intents 识别指令 |
| `clipwright/schema/agent.py` | 修改 | 新增 `AnimationIntent`、扩展 `RequirementsOutput`/`AnimationOutput` |
| `docs/api_reference.md` | 更新 | 新增插件 API (`POST /api/plugin/llm_mg/save-template`) |
| `docs/development.md` | 更新 | 新增 MG 插件开发说明 |
