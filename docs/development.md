# 帧艺 ClipWright 内容视频编排引擎 — 开发指南

## 技术栈

- **语言**: Python >= 3.12
- **框架**: FastAPI
- **数据模型**: Pydantic v2
- **LLM 集成**: IsoBase（Anthropic Claude / OpenAI / Ollama）
- **Agent 编排**: 动态路由 DAG（v2）+ 并行 + 自愈循环
- **视频处理**: FFmpeg + ffprobe
- **配置**: pydantic-settings（环境变量驱动）

## 开发环境

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 启动开发服务器（热重载）
uvicorn clipwright.main:app --reload --host 0.0.0.0 --port 8000
```

## 项目结构

```
clipwright/                  # 核心引擎
├── agents/                  # 7 个 Agent
│   ├── requirements_agent.py # 需求分析
│   ├── structure_agent.py   # 结构分析
│   ├── material_agent.py    # 素材搜索
│   ├── edit_agent.py        # 时间线生成
│   ├── animation_agent.py   # 动画编排（文字/逻辑/MG/过渡）
│   ├── audio_agent.py       # 音频处理（BPM/BGM/音量)
│   ├── quality_agent.py     # 质检 + 自愈
│   └── base.py              # Agent 基类
├── api/                     # REST API 端点 (30 路由)
├── services/                # 核心服务
│   ├── pipeline.py          # 管线 v1（固定序列）
│   ├── pipeline_v2.py       # 管线 v2（DAG 并行 + 熔断 + 自愈）
│   ├── agent_bus.py         # Agent 上下文总线
│   ├── render.py            # 渲染引擎（多轨/缓存/降级）
│   ├── task_queue.py        # 并发任务队列（信号量+超时）
│   ├── trace.py             # 执行追踪（含 TTL/内存保护）
│   ├── tracing_service.py   # SpanTracer（MongoDB 持久化）
│   └── llm.py               # LLM 服务（Anthropic/OpenAI/Ollama）
├── animation/               # 动画渲染
│   ├── catalog.py           # 动画目录（18 文字 + 10 逻辑 + 9 过渡）
│   ├── renderer.py          # 动画渲染器（8 种缓动函数）
│   ├── hyperframes_renderer.py # Hyperframes HTML→MP4 集成
│   ├── mg_renderer.py       # MG 动画（HTML/CSS → MP4）
│   ├── diagram_svg.py       # SVG 图解渲染器（24+ 图表类型）
│   └── registry.py          # 动画注册表
├── tool/                    # 原子能力
├── skill/                   # 12 个 Skill
├── category/                # 类型插件（内置 4 种）
├── plugins/                 # 第三方插件系统
│   ├── loader.py            # 插件发现/加载/生命周期
│   ├── base.py              # 插件基类（5 种类型）
│   └── hooks.py             # Hook 系统（9 个 HookPoint）
├── rag/                     # RAG 管线
├── persona/                 # Persona 加载/验证/继承
├── schema/                  # Pydantic 数据模型
└── context/                 # MongoDB 连接上下文

PluginData/                  # 插件运行时数据目录
├── tmp/                     # 渲染中间文件
├── assets/                  # 素材副本
├── cache/                   # 工具缓存
├── thumbs/                  # 缩略图缓存
└── plugins/                 # 各插件专属数据目录
    └── <plugin_id>/         # 自动创建

plugins/                     # 第三方插件安装目录
renders/                     # 最终 MP4 输出
library/                     # 素材库（上传的素材文件）
personas/                    # Persona 定义
```

## PluginData 目录规范

所有插件产生的运行时数据（配置快照、缓存、生成文件等）必须写入 `PluginData/`，**不能写入插件自身的安装目录**。

```
PluginData/
├── tmp/                     # 渲染中间件、临时文件
├── assets/                  # 插件生成的媒体素材
├── cache/                   # 工具调用缓存
├── thumbs/                  # 缩略图缓存
└── plugins/<plugin_id>/     # 各插件专属数据目录（自动创建）
    ├── config/              #   插件配置快照
    ├── cache/               #   插件缓存
    └── output/              #   插件输出
```

在 PluginLoader 中通过 `loader.get_plugin_data_dir("my_plugin_id")` 获取路径。

### config.yaml 约定

每个插件的运行时配置**统一为 `config.yaml`**。前端可编辑的配置覆盖项存储于：

```
PluginData/plugins/{plugin_id}/config.yaml
```

插件加载时，PluginLoader 会合并两个配置源：
1. **源码默认配置**：`plugins/{plugin_id}/config.yaml`（插件作者提供）
2. **运行时覆盖配置**：`PluginData/plugins/{plugin_id}/config.yaml`（前端编辑）

合并规则：顶级键覆盖（非递归深合并）。数据目录配置不存在时，仅使用源码默认值。前端可通过 `DELETE /api/plugin/{id}/config` 删除数据目录文件，回退到源码默认值。

插件内通过 `self.config` 访问合并后的配置。

## 新增一个第三方插件

```bash
# 1. 创建插件目录
mkdir -p plugins/my_plugin
cd plugins/my_plugin

# 2. 创建 plugin.yaml 清单
cat > plugin.yaml << 'EOF'
name: "我的插件"
version: "1.0.0"
kind: "capability"
entry_point: "my_plugin.main"
EOF

# 3. 创建 main.py
cat > main.py << 'PYEOF'
from clipwright.plugins import BasePlugin

class MyPlugin(BasePlugin):
    def initialize(self):
        # 通过 PluginLoader 获取专属数据目录
        from clipwright.main import get_plugin_loader
        data_dir = get_plugin_loader().get_plugin_data_dir(self.plugin_id)
        # data_dir = PluginData/plugins/my_plugin/
        (data_dir / "output").mkdir(parents=True, exist_ok=True)
PYEOF
```

详细规范见 `clipwright/plugins/loader.py`。

## 新增一个 Tool

```python
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具说明"
    dependencies = ["ffmpeg"]

    async def execute(self, input_path: str = "", **kwargs) -> ToolExecResult:
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={})

# 在 tool/__init__.py 的 register_builtin_tools() 中添加
```

**已注册的 Tool**：`video_trim` `video_concat` `video_overlay` `video_download` `video_crop` `video_thumbnail` `video_speed` `video_blur` `media_probe` `audio_extract` `audio_normalize` `audio_mix` `audio_replace` `bpm_detect` `scene_detect` `semantic_match` `vision_llm` `face_detect` `background_remove` `effect_vignette` `watermark` `video_filter` `chroma_key` `video_stabilize` `generate_text_video` `subtitle_burn` `text_design` `typewriter_animation` `tracking_text` `mg_dynamic` `material_filter` `frame_validator` `black_frame_detect` `audio_silence_detect` `subtitle_overflow` `speed_ramp` `color_correct` `lut_apply` `whisper_transcribe` `transition_apply` `voice_clone` `text_to_speech`

## 新增一个 Skill

```python
from clipwright.skill.base import BaseSkill
from clipwright.schema.skill import SkillExecResult, SkillStatus

class MySkill(BaseSkill):
    name = "my_skill"
    description = "技能说明"
    required_tools = ["my_tool"]

    async def execute(self, **kwargs) -> SkillExecResult:
        result = await self._run_tool("my_tool", **kwargs)
        return SkillExecResult(status=SkillStatus.SUCCESS, skill_name=self.name, output=result)

# 在 skill/builtin.py 的 register_builtin_skills() 中添加
```

**已注册的 Skill**：`analyze_video_structure` `generate_captions` `analyze_audio_rhythm` `auto_caption` `broll_matcher` `script_analysis` `material_downloader` `voiceover_sync` `auto_transition` `background_music` `silence_cut` `dub_script`

## 新增一个 Agent

1. 创建 `clipwright/agents/my_agent.py`，继承 `BaseAgent`
2. 在 `clipwright/agents/__init__.py` 导出
3. 在 `PipelineOrchestratorV2._agents` 字典和 `_dispatch` 方法中注册
4. 在 `AgentDAG._DEPS` 中添加依赖关系

## 用户自定义视频类型

```bash
curl -X POST http://localhost:8000/api/type-maker/create \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "my_type", "display_name": "我的类型", ...}'
```

存储在 `user_types/my_type.json`，重启后自动加载。

## 模板批量生成

```bash
curl -X POST http://localhost:8000/api/template/batch/my_template \
  -H "Content-Type: application/json" \
  -d '[{"topic": "视频1"}, {"topic": "视频2"}]'
```

## 测试

```bash
pytest tests/ -v

# 只运行非 e2e 测试（更快）
pytest tests/clipwright/ -v

## MG 动画插件开发

`plugins/llm_mg/` 提供 LLM 驱动的 MG 动画生成能力。

### 插件结构

```
plugins/llm_mg/
├── plugin.yaml       # 插件清单
├── config.yaml       # LLM prompt + 生成配置
├── main.py           # LLMMGPlugin 主类
├── generator.py      # LLM 调用 + 验证 + 修复 + 降级
├── validator.py      # MG JSON schema 验证 + 自动修复
├── fallback.py       # 降级策略（语义 → 模板匹配）
├── storage.py        # 生成持久化 + 保存为模板
└── templates/        # 可复用 MG JSON 模板
```

### 添加新模板

在 `plugins/llm_mg/templates/` 创建 JSON 文件，参考 `mg_title_reveal.json` 格式。

### 自定义 LLM prompt

编辑 `plugins/llm_mg/config.yaml` 中的 `prompt.system_template`。

### 插件接口

```python
from clipwright.plugins import PluginLoader

plugin = PluginLoader().get("llm_mg")
result = await plugin.generate_mg(
    description="产品对比动画",
    text_content="A产品|B产品|A胜出",
    persona_style={"primary_color": "#4f8cff"},
    scene_context={"title": "性能对比", "keywords": ["CPU", "GPU"]},
)
# result: {"success": bool, "html": str, "mg_def": dict, "method": "llm|fallback", "generation_id": str}
```

### 降级策略

```
LLM 生成 MG JSON
  ├── 成功 → MGRenderer.render() → Hyperframes MOV
  ├── JSON 校验失败 → 自动修复 → 仍失败则模板匹配
  └── LLM 不可用 → 关键词匹配已有模板 → drawtext 最终降级
```
```
