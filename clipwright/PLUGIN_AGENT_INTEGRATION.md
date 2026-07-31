# ClipWright 插件开发指南 — Agent 集成

## 概述

插件可以通过 `PluginPromptRegistry` 向核心 Agent 注入提示词，
让 Agent 在生成内容时了解并使用插件提供的能力。
所有提示词在 Agent 构建 system prompt 时自动聚合。

## 快速开始

```python
from clipwright.plugins.prompt_registry import PluginPromptRegistry

class MyPlugin(CapabilityPlugin):
    def initialize(self) -> None:
        # 注册提示词到 StructureAgent
        PluginPromptRegistry.register(
            plugin_id="my_plugin",       # 插件 ID
            agent_name="structure",       # 目标 Agent
            prompt="## 我的能力\n...",    # 提示词内容
            priority=5,                   # 优先级（越大越靠前）
            description="简短说明"         # 可选
        )
```

## 目标 Agent 列表

| Agent 名称 | 文件 | 作用 | 何时注入 |
|-----------|------|------|----------|
| `structure` | `agents/structure_agent.py` | 场景脚本骨架生成 | 生成场景前 |
| `animation` | `agents/animation_agent.py` | 动画创建 | 处理动画标记时 |
| `material` | `agents/material_agent.py` | 素材搜索 | 生成搜索关键词时 |
| `requirements` | `agents/requirements_agent.py` | 需求收集/简报 | 生成创意简报时 |

EditAgent / AudioAgent / QualityAgent 不使用 LLM，不接受提示词注入。

## 提示词编写指南

### 1. 声明能力（What）

清晰说明你的插件提供了什么能力：

```python
prompt="## LLM 动态 MG 动画（来自 my_plugin）\n支持从自然语言描述生成数据可视化、对比图等。"
```

### 2. 说明用途（When）

明确在什么场景下应该使用你的能力：

```python
"何时使用：场景涉及数据/对比/流程时，必须使用 mg_dynamic 标记。"
```

### 3. 给出格式（How）

提供具体的标记格式或调用方式：

```python
"标记格式：[逻辑动画]mg_dynamic:{\"description\":\"...\",\"text\":\"A|B\"}"
```

### 4. 设置优先级

- `priority=10`：核心能力，必须在 prompt 顶部显示（如 llm_mg）
- `priority=5`：重要扩展，在核心能力之后（如 animation 类插件）
- `priority=1-3`：通用增强，放在最后（如 Lottie 动画）

## 完整示例：llm_mg 插件

```python
class LLMMGPlugin(CapabilityPlugin):
    def initialize(self) -> None:
        # ... 初始化生成器 ...

        from clipwright.plugins.prompt_registry import PluginPromptRegistry

        PluginPromptRegistry.register(
            "llm_mg", "structure",
            "## LLM 动态 MG 动画（mg_dynamic）\n\n"
            "与文字动画用途不同，二者互不冲突：\n"
            "- 文字动画：强调标题/字幕/关键句\n"
            "- MG 动画：数据可视化/对比图/流程图\n\n"
            "标记格式：[逻辑动画]mg_dynamic:{...}\n"
            "使用场景：数据对比、进度展示、标题特效、关系图",
            priority=10,
        )

        PluginPromptRegistry.register(
            "llm_mg", "animation",
            "## MG 动画生成指引\n"
            "处理 mg_dynamic 标记时：\n"
            "- 使用内置 MGGenerator 生成 HTML/CSS\n"
            "- 风格参数来源于 Persona visual_config",
            priority=5,
        )
```

## 插件中提示词的卸载

```python
def shutdown(self) -> None:
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    PluginPromptRegistry.unregister("my_plugin")
```

## Agent 的提示词注入机制

每个使用 LLM 的 Agent 在执行时：

1. 构建自身 system prompt
2. 调用 `PluginPromptRegistry.get_for_agent("agent_name")`
3. 将返回的提示词列表追加到 system prompt 的插件能力区域

```python
# 以 StructureAgent 为例：
system_prompt = SYSTEM_PROMPT_TPL.format(...)
system_prompt += TOOL_PROMPT
system_prompt += persona_prompt

# ★ 插件提示词注入槽位
from clipwright.plugins.prompt_registry import PluginPromptRegistry
plugin_prompts = PluginPromptRegistry.get_for_agent("structure")
if plugin_prompts:
    system_prompt += "\n\n## 插件能力\n" + "\n\n".join(plugin_prompts)
```

## 插件工具暴露给 Agent（LLM tool-use）

Agent（如 StructureAgent）会动态收集所有声明 `agent_callable=True` 的工具，
暴露给 LLM 主动调用。**插件工具不再需要硬编码进 Agent 的工具列表。**

```python
from clipwright.tool.base import BaseTool

class MyAIGenTool(BaseTool):
    name = "my_ai_generate"
    agent_callable = True   # ★ 声明可被 Agent LLM 调用
    description = "根据提示生成..."
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "提示词"},
        },
        "required": ["prompt"],
    }

    async def execute(self, prompt: str, **kwargs):
        ...
```

注册后即可被 Agent 使用：

```python
# 插件 initialize() 中
from clipwright.tool.registry import ToolRegistry
ToolRegistry.register(MyAIGenTool(), plugin_id="my_plugin")
```

### 规则

- `agent_callable = True`：该工具进入 Agent 的 LLM tool-use 列表（需 `is_available()`）
- 默认 `False`：仅编排路径可调用（如 `ToolRegistry.execute`），不暴露给 LLM，
  避免参数复杂的原子能力（video_trim 等）被 LLM 误调用
- Agent 用 `ToolRegistry.list_agent_callable()` 动态收集，插件加载/卸载即时生效

## 注意事项

1. **提示词不要过长**：控制在 500 字以内，避免 token 膨胀
2. **不要重复基本信息**：插件通过 Hook 注册的内容（如动画目录）不要再次在提示词中列出
3. **文字动画 ≠ MG 动画**：它们是不同用途的能力，提示词应体现这一点
4. **优先使用工具**：如果插件有对应的 Tool 注册，提示词中应引导 Agent 调用工具获取最新信息
5. **测试提示词效果**：通过 E2E 测试验证 Agent 是否正确理解了插件能力
