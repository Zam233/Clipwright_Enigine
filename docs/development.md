# 帧艺 ClipWright 内容视频编排引擎 — 开发指南

## 技术栈

- **语言**: Python >= 3.12
- **框架**: FastAPI + LangGraph
- **数据模型**: Pydantic v2
- **LLM 集成**: IsoBase（支持 Anthropic Claude / OpenAI / Ollama）
- **配置**: pydantic-settings（环境变量驱动）
- **测试**: pytest + pytest-asyncio
- **代码质量**: ruff（lint）+ mypy（type check）

## 开发环境

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 启动开发服务器（热重载）
uvicorn clipwright.main:app --reload --host 0.0.0.0 --port 8000
```

## 测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/clipwright/test_schema.py -v

# 运行特定测试类
pytest tests/clipwright/test_persona_forge.py::TestPersonaForgeBuild -v
```

## 代码规范

```bash
# Lint 检查
ruff check clipwright/

# 类型检查
mypy clipwright/
```

## 项目约定

### 包组织

```
clipwright/
├── schema/     # Pydantic 数据模型（前后端统一契约）
├── persona/    # Persona 系统
├── category/   # 视频类型插件
├── agents/     # Agent 编排层
├── plugins/    # 第三方插件系统
├── services/   # 业务服务层
├── tool/       # 原子能力层
└── api/        # FastAPI 路由
```

### Agent 开发规范

1. 每个 Agent 继承 `BaseAgent[InputType, OutputType]`
2. Agent 的 `execute()` 方法必须是 async
3. Agent 输入/输出必须有对应的 Pydantic 模型（定义在 `schema/agent.py`）
4. Agent 内部不直接调用原子能力，经 `category_plugin.translate_persona()` 翻译
5. 需要 LLM 调用工具的 Agent，使用 `AgentToolkit` + `LLMService.with_tools()`：

```python
from clipwright.tool.base import AgentToolkit
from clipwright.tool.registry import ToolRegistry

# 构建工具包
toolkit = AgentToolkit(
    tool_names=ToolRegistry.list_available_names(),
    fmt="anthropic",  # 或 "openai"
)

# 在 LLM 带工具调用的推理中使用
resp = await self._llm.with_tools(
    system_prompt=system_prompt,
    user_prompt=user_prompt,
    tool_executor=self._tool_executor,  # async回调
    tools=toolkit.llm_tools,            # 自动生成的 schemas
)
```

### 添加视频类型插件

1. 在 `clipwright/category/` 下新建文件
2. 继承 `BaseCategoryPlugin`，实现 `translate_persona()` 和 `get_shot_params()`
3. 在 `clipwright/main.py` 的 `_register_builtin_plugins()` 中注册

### 添加视频类型插件

1. 在 `clipwright/category/` 下新建文件
2. 继承 `BaseCategoryPlugin`，实现 `translate_persona()` 和 `get_shot_params()`
3. 在 `clipwright/main.py` 的 `_register_builtin_plugins()` 中注册

### 添加第三方插件

第三方插件放置在项目根目录的 `plugins/{plugin_id}/` 下，自动被 `PluginLoader` 发现加载：

```
plugins/{plugin_id}/
├── plugin.yaml       # 清单：id, name, version, kind, entry_point
├── main.py           # 入口模块（需 export 插件类到 __all__）
└── ...                # 其他依赖
```

**约束**：
- 入口模块的 `__all__` 中 export 的类会被自动实例化
- 插件类需继承 `clipwright.plugins` 中的基类（`BasePlugin` / `MaterialSourcePlugin` / `AgentStrategyPlugin` / `CapabilityPlugin`）
- `initialize()` 在加载时调用 — **在此注册插件提供的 Tool 和 Skill**：
  ```python
  def initialize(self) -> None:
      ToolRegistry.register(MyTool())
      SkillRegistry.register(MySkill())
  ```
- `shutdown()` 在卸载时调用 — 在此清理注册的内容

### 添加技能（Skill — 可组合的高级能力）

技能在 `clipwright/skill/` 下实现，继承 `BaseSkill`，可编排多个 Tool：

```python
from clipwright.skill.base import BaseSkill
from clipwright.schema.skill import SkillExecResult, SkillStatus

class MySkill(BaseSkill):
    name = "my_skill"
    description = "编排多个工具完成分析"
    required_tools = ["scene_detect", "bpm_detect"]

    async def execute(self, video_path: str, **kwargs) -> SkillExecResult:
        # 内部调用工具
        scene = await self._run_tool("scene_detect", input_path=video_path)
        bpm = await self._run_tool("bpm_detect", input_path=audio_path)
        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={"scenes": scene, "bpm": bpm},
        )
```

技能通过 `SkillRegistry.register()` 注册，与 Tool 共享同一套 LLM tool schema 接口。

### 添加工具（原子能力）

工具在 `clipwright/tool/` 下实现，继承 `BaseTool`：

```python
from clipwright.tool.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "我的工具"
    dependencies = ["ffmpeg"]  # 可选，用于可用性检测

    async def execute(self, input_path: str, **kwargs) -> ToolExecResult:
        # 实现逻辑...
```

工具通过 `register_builtin_tools()` 自动注册到全局 `ToolRegistry`，可通过 REST API `/api/tool/` 访问，也可在 Agent 中通过 `ToolRegistry.execute("my_tool", ...)` 调用。

### 添加 API 端点

1. 在 `clipwright/api/` 下新建路由文件
2. 使用 `APIRouter` 定义路由
3. 在 `clipwright/main.py` 中 `include_router()`
4. 更新 `docs/api_reference.md`

## 关键设计约束

1. **Persona 配置层不直接调用原子能力，必须经过类型插件层翻译**
2. **所有工具 API 的入参必须是纯数值或纯路径，不接受风格描述字符串**
3. **Agent 是调度器，不写死逻辑** — 通过策略注册表选择行为
4. **时间线 JSON 格式是前后端唯一的数据契约**
