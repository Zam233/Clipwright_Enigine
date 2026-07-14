# 帧艺 ClipWright 内容视频编排引擎 — 快速开始

## 环境要求

- Python >= 3.12
- FFmpeg + ffprobe（必需，所有视频处理依赖）
- Anthropic API Key 或 OpenAI API Key（StructureAgent 必需）
- 可选: edge-tts（TTS 配音，`pip install edge-tts`）

## 安装

```bash
git clone <repo-url>
cd clipwright
pip install -e ".[dev]"
```

## 配置

通过 `.env` 文件配置 LLM。配置文件位于 `clipwright/.env`。

### LLM 配置

```bash
# OpenAI 兼容 API（DeepSeek / vLLM 等）
CLIPWRIGHT_LLM_PROVIDER=openai
CLIPWRIGHT_LLM_API_KEY=sk-...
CLIPWRIGHT_LLM_BASE_URL=https://api.deepseek.com
CLIPWRIGHT_LLM_MODEL=deepseek-chat
```

## 启动

```bash
uvicorn clipwright.main:app --reload --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000/test` 访问测试前端。

## 快速测试

### 运行一次完整管线

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "topic": "年轻人的盲盒消费"
  }'
```

### 运行管线 v2（动态路由 + 并行）

```bash
curl -X POST http://localhost:8000/api/pipeline/run-v2 \
  -H "Content-Type: application/json" \
  -d '{"persona_id": "zam_knowledge_critical", "category_plugin_id": "knowledge_longform", "topic": "年轻人的盲盒消费"}'
```

### 从文稿生成视频

```bash
# 先创建编辑会话
curl -X POST http://localhost:8000/api/edit/session/create -H "Content-Type: application/json" \
  -d '{"pipeline_id": "pl_test"}'

# 然后运行管线（传入文稿）
curl -X POST http://localhost:8000/api/pipeline/run-async \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "topic": "主题",
    "extra_params": {
      "script_text": "今天我们来聊聊...",
      "audio_duration_sec": 600,
      "video_mode": "voiceover"
    }
  }'
```

### 视觉模式（李子柒风格）

```json
{
  "extra_params": {
    "video_mode": "visual",
    "script_text": "清晨竹林里砍竹子\n用竹条编织篮子\n把篮子放入溪水中浸泡",
    "orientation": "landscape"
  }
}
```

## 智能预判

```bash
# 分析文稿，自动推荐配置
curl -X POST "http://localhost:8000/api/pipeline/predict-script?script_text=今天我们来聊聊年轻人的盲盒消费..."
# → 返回: 估算时长、推荐 Persona、推荐类型插件、情绪基调

# 分析素材，推荐使用方式
curl -X POST "http://localhost:8000/api/pipeline/predict-material?file_path=/path/to/video.mp4"
# → 返回: 时长、分辨率、方向、用途建议
```

## Undo/Redo

```bash
# 创建版本快照
curl -X POST "http://localhost:8000/api/learn/version/session_1/snapshot" \
  -H "Content-Type: application/json" \
  -d '{"data": {"timeline": {...}}, "label": "第一次编辑"}'

# 撤销
curl -X POST "http://localhost:8000/api/learn/version/session_1/undo"

# 重做
curl -X POST "http://localhost:8000/api/learn/version/session_1/redo"
```

## 自适应 Persona

每次编辑自动记录偏好，后续生成时自动应用：

```bash
# 记录编辑
curl -X POST "http://localhost:8000/api/learn/persona/zam_knowledge_critical/record?action=apply_video_filter" \
  -H "Content-Type: application/json" \
  -d '{"params": {"brightness": 0.1, "contrast": 1.2}}'

# 查看学习到的偏好
curl http://localhost:8000/api/learn/persona/zam_knowledge_critical/preferences
```

## 项目目录

```
clipwright/          # 核心引擎代码
plugins/             # 第三方插件安装目录
PluginData/          # 插件运行时数据（自动生成）
  tmp/               #   渲染中间文件
  assets/            #   素材副本
  cache/             #   工具缓存
  plugins/           #   各插件专属目录
renders/             # 最终 MP4 输出
library/             # 素材库文件
personas/            # Persona 定义
```

## 核心概念

| 概念 | 说明 |
|------|------|
| **Pipeline** | Agent 编排管线，v1 固定序列 / v2 动态并行 |
| **Agent** | 6 个 Agent（结构/素材/剪辑/动画/音频/质检） |
| **Tool** | 原子能力（FFmpeg 封装） |
| **Skill** | 可组合的高级能力 |
| **Persona** | UP 主风格配置（四层复合 + 自适应学习） |
| **Type Plugin** | 视频类型插件（内置 + 第三方） |
| **PluginData** | 插件运行时数据统一目录 |
| **Template** | 带 `{{变量}}` 占位符的可复用模板 |
| **Type Maker** | 用户自定义视频类型的制作器 |

## 对话式编辑

```bash
# 创建编辑会话（关联已有管线结果）
curl -X POST http://localhost:8000/api/edit/session/create

# 发送编辑指令
curl -X POST "http://localhost:8000/api/edit/session/{session_id}/chat?message=调亮画面，加暗角效果"
```

## 更多

- [架构设计](structure.md)
- [Agent 工作流](workflow.md)
- [Persona 系统](Persona.md)
- [API 参考](api_reference.md)
- [开发指南](development.md)
