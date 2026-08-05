# 帧艺 ClipWright 用户指南

> 本文档面向**非技术用户**，说明 ClipWright 后端引擎的核心功能和使用方式。

---

## 一、ClipWright 是什么？

ClipWright 是一个 AI 视频编排引擎。你提供**文稿或场景描述**，它自动完成：

```
文稿/场景 → AI 分析结构 → 搜索素材 → 生成时间线 → 添加动画/音频 → 渲染成片
```

支持两种创作模式：

| 模式 | 适用场景 | 输入示例 |
|------|---------|----------|
| **口播模式** | 知识解说、评测、Vlog 旁白 | 完整的配音文稿 |
| **视觉模式** | 李子柒风格、无配音纯画面叙事 | 每行一个场景描述 |

---

## 二、快速上手

### 方式 1：通过测试前端 (Web UI)

1. 启动服务：`uvicorn clipwright.main:app --reload --port 8000`
2. 打开浏览器访问 `http://localhost:8000/test`
3. 点击左侧 **剪辑工作台**
4. 填写选题、选择模式（口播/视觉）、粘贴文稿或场景描述
5. 点击"生成视频"→ 等待管线执行 → 点击"渲染 MP4"→ 下载成片

### 方式 2：通过 API 调用

```bash
# 1. 先分析文稿，获取推荐配置
curl -X POST "http://localhost:8000/api/pipeline/predict-script?script_text=今天我们来聊聊..."

# 2. 启动管线
curl -X POST http://localhost:8000/api/pipeline/run-async \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "topic": "你的选题",
    "extra_params": {
      "script_text": "你的完整文稿...",
      "video_mode": "voiceover"
    }
  }'

# 3. 获取结果
curl http://localhost:8000/api/pipeline/result/{pipeline_id}
```

---

## 三、核心功能

### 3.1 视频生成管线

一次标准的视频生产流程包含 6 个 Agent：

```
[1] 结构 Agent     → 分析文稿，生成带时间结构的场景列表
[2] 素材 Agent     → 根据场景描述搜索匹配的视频/图片素材
[3] 剪辑 Agent     → 将素材裁剪、拼接为完整时间线
[4] 动画 Agent     → 添加文字动画、转场效果
[5] 音频 Agent     → 匹配背景音乐、调节音量
[6] 质检 Agent     → 检查视频质量，发现问题自动回退修复
```

v2 版（推荐）支持**并行执行**：素材搜索和音频分析同时进行，出片更快。

### 3.2 两种创作模式

**口播模式**：用于有配音解说的视频。

```
输入: 完整的口播文稿
流程: 文稿 → 按标点拆分为字幕段 → 对齐配音 → 分场景 → 匹配素材 → 剪辑
输出: 带配音、字幕、BGM 的完整视频
```

**视觉模式**：用于无配音、纯画面叙事的视频（如李子柒风格）。

```
输入: 每行一个场景描述
  清晨竹林里砍竹子
  用竹条编织篮子
  把篮子放入溪水中浸泡
流程: 每行作为一个独立场景 → 搜索匹配素材 → 按顺序拼接
输出: 无配音、纯画面的视频
```

### 3.3 素材智能匹配

素材 Agent 会自动：

1. **生成具体搜索词** — LLM 将抽象概念（如"消费主义"）转为具体视觉词（如"购物商场""商品展示"）
2. **多组关键词分别搜索** — 提高命中率
3. **帧验证** — 抽取视频帧进行验证，过滤全黑/全白画面
4. **方向偏好** — 优先匹配横屏/竖屏素材

搜索范围来自注册的素材源，如 Pexels 免费视频库、本地素材目录等。

### 3.4 渲染输出

支持多种导出预设：

| 预设 | 分辨率 | 帧率 | 码率 | 适用平台 |
|------|--------|------|------|---------|
| `bilibili` | 1920×1080 | 30fps | 6Mbps | B站 |
| `youtube` | 1920×1080 | 30fps | 8Mbps | YouTube |
| `tiktok` | 1080×1920 | 30fps | 4Mbps | 抖音/快手 |
| `weibo` | 720×1280 | 24fps | 3Mbps | 微博 |
| `1080p` | 1920×1080 | 30fps | 5Mbps | 标准高清 |
| `720p` | 1280×720 | 30fps | 3Mbps | 标准标清 |

渲染时支持：
- **多轨混音**：配音 + 背景音乐，BGM 自动降低音量避免压过语音
- **画中画 (PiP)**：叠加轨道自动合成到主视频
- **文字特效**：支持描边、发光、阴影、自定义字体
- **统一缩放**：不同分辨率的素材自动缩放到目标尺寸
- **队列渲染**：多个渲染任务排队执行，完成后可下载

---

## 四、高级功能

### 4.1 对话式编辑

生成视频后，可以通过自然语言指令修改：

```bash
# 创建编辑会话
curl -X POST http://localhost:8000/api/edit/session/create

# 发送修改指令
curl -X POST "http://localhost:8000/api/edit/session/{id}/chat?message=把字幕改成金色粗体加发光效果"
```

支持的编辑指令：

| 指令示例 | 执行的动作 |
|---------|-----------|
| "调亮画面" | 调用 VideoFilterTool 增加亮度 |
| "加暗角效果" | 调用 EffectVignetteTool |
| "字幕改成金色粗体" | 调用 TextDesignTool 修改文字样式 |
| "在右下角加水印" | 调用 WatermarkTool |
| "把这段放慢到 0.5 倍" | 调用 VideoSpeedTool |
| "把这个场景的转场改成渐隐" | 调用 TransitionApplyTool |

### 4.2 模板系统

创建可复用的视频模板，支持 `{{变量}}` 占位符：

```json
{
  "template_id": "product_review",
  "name": "产品评测模板",
  "topic_template": "{{product_name}} 深度评测",
  "script_template": "今天我们来评测 {{product_name}}...（{{feature}} 是最大亮点）"
}
```

批量生成：

```bash
curl -X POST http://localhost:8000/api/template/batch/product_review \
  -H "Content-Type: application/json" \
  -d '[{"product_name": "iPhone 16", "feature": "拍照"}, {"product_name": "MacBook Air", "feature": "续航"}]'
```

### 4.3 类型制作器

自定义视频类型，无需写代码。在"类型制作器"页面配置：

- 剪辑节奏（平稳/快速/张弛有度）
- 动画密度（低/中/高）
- 镜头时长（低密度/中密度/高密度）
- 转场偏好（各种转场类型的权重）
- 标注模板

### 4.4 自适应 Persona

每次编辑操作都会记录到 Persona 学习器。系统会自动学习你的偏好：

- **亮度/对比度偏好** — 你每次调亮 0.1，系统会记录并建议未来的默认亮度
- **转场偏好** — 你频繁使用某种转场，系统会提高它的权重
- **文字样式偏好** — 你总是用金色粗体，系统会在后续视频中默认使用

查看学习到的偏好：

```bash
curl http://localhost:8000/api/learn/persona/zam_knowledge_critical/preferences
```

### 4.5 Undo/Redo (版本管理)

每次编辑可以创建版本快照，支持撤销和恢复到任意历史版本：

```bash
# 创建快照
curl -X POST "http://localhost:8000/api/learn/version/my_session/snapshot" \
  -H "Content-Type: application/json" \
  -d '{"data": {"timeline": {...}}, "label": "第一次调色后"}'

# 查看所有版本
curl http://localhost:8000/api/learn/version/my_session/list

# 对比两个版本的差异
curl "http://localhost:8000/api/learn/version/my_session/diff?pos_a=0&pos_b=1"

# 撤销
curl -X POST http://localhost:8000/api/learn/version/my_session/undo
```

---

## 五、素材管理

### 上传素材

```bash
# 单文件上传
curl -X POST http://localhost:8000/api/asset/upload \
  -F "file=@/path/to/video.mp4"

# 批量上传
curl -X POST http://localhost:8000/api/asset/upload-batch \
  -F "files=@video1.mp4" -F "files=@video2.mp4"
```

上传后自动：
1. **探测元数据** — 时长、分辨率、编码
2. **分类打标签** — 根据文件名和类型自动生成标签
3. **加入预处理队列** — 后台转码代理 + 场景检测 + 音频提取

### 注册素材源

系统内置 Pexels 免费视频库素材源。也可以通过插件系统注册自己的素材源。

---

## 六、视频质量标准

渲染完成后，可以用质检工具自动检查：

```bash
# 检测黑帧/全白帧
curl -X POST http://localhost:8000/api/tool/execute?name=black_frame_detect \
  -H "Content-Type: application/json" \
  -d '{"params": {"video_path": "renders/output.mp4"}}'

# 检测音频静音段
curl -X POST http://localhost:8000/api/tool/execute?name=audio_silence_detect \
  -H "Content-Type: application/json" \
  -d '{"params": {"audio_path": "renders/output.mp4"}}'

# 检查字幕溢出
curl -X POST http://localhost:8000/api/tool/execute?name=subtitle_overflow \
  -H "Content-Type: application/json" \
  -d '{"params": {"subtitles": [...]}}'
```

---

## 七、事件通知 (Webhook)

订阅管线/渲染完成通知：

```bash
curl -X POST "http://localhost:8000/api/webhook/subscribe?event_type=pipeline&url=https://your-server.com/callback"
```

```bash
curl -X POST "http://localhost:8000/api/webhook/subscribe?event_type=render&url=https://your-server.com/callback"
```

支持的事件：`pipeline`（管线完成）、`render`（渲染完成）

---

## 八、常见问题

**Q: 视频时长与配音不匹配？**
A: 系统会自动将文稿时长缩放至配音时长。如果 STT 对齐失败，会使用 ffprobe 探测到的音频文件实际时长。

**Q: 渲染出来的视频没有声音？**
A: 需要确保配音文件路径正确。在渲染请求中传入 `audio_file_path` 参数指定配音文件路径。

**Q: 渲染出来的视频画面大小不一？**
A: 渲染器会自动将所有素材缩放到目标分辨率（默认 1920×1080），并在画面不足时填充黑边。

**Q: 素材太少，时间线不够长？**
A: 剪辑 Agent 会自动循环使用已有素材填充场景时长。如果素材完全不可用，会自动生成文字占位视频。

**Q: 如何让 AI 学习我的编辑偏好？**
A: 每次编辑操作通过 `POST /api/learn/persona/{id}/record` 记录后，系统会自动学习并更新 Persona 参数。

---

> 完整 API 列表见 [api_reference.md](api_reference.md)
> 开发指南见 [development.md](development.md)
