# 动画背景与工作区素材能力 PRD

- 状态：Draft
- 关联：`docs/animation-agentic-visual-loop-plan.md`
- 目标：让 AnimationAgent 真正拥有“读工作区选图”和“自主决定背景”的能力

## 1. 背景

当前：

- AnimationAgent 的图片来自 `input.image_assets` / 素材检索 / `web_images`；
- 不能自己浏览工作区、读文件、选图；
- `MGGenerator._ensure_no_background()` 无条件清除不透明背景；
- LLM 即使生成了背景，也会被工具层改回 transparent；
- 动画颜色决策时看不到它将被叠加的真实画面。

结果：

```text
LLM 按 palette 选黑字
→ MG 背景被清成 transparent
→ 叠在深色实拍上不可读
```

## 2. 目标能力

### 2.1 工作区读取

新增 Agent 工具：

```text
workspace_tree(root?, glob?, include_dirs?)
read_file(path, mode=text|image|binary)
```

`read_file` 对图片返回：

```json
{
  "path": "materials/bg/keyboard.png",
  "kind": "image",
  "mime": "image/png",
  "width": 1920,
  "height": 1080,
  "artifact_ref": "artifact://...",
  "thumbnail_ref": "artifact://..."
}
```

不把二进制塞进 LLM 上下文；只给 artifact ref + 元数据。

### 2.2 背景决策权交给 LLM

`_ensure_no_background` 增加显式参数：

```python
_ensure_no_background(
    mg_def,
    vision_prompt="",
    *,
    allow_opaque_background: bool = False,
    background_reason: str = "",
)
```

规则：

```text
LLM 明确选择背景（allow_opaque_background=true 且有 background）
→ 保留
否则
→ 清除不透明背景，回到 transparent / scrim
```

`generate_mg` 工具 schema 增加：

```json
{
  "background_mode": "transparent|color|image",
  "background_value": "#0E2238 | materials/bg.png",
  "background_reason": "..."
}
```

不再由 generator 偷偷覆盖。

### 2.3 提示词约束

新增：

```text
默认透明背景。
只有满足以下之一才添加背景：
1. 用户明确要求；
2. 需求/结构/主 Agent 明确要求；
3. 添加背景能显著改善文字可读性/信息层级。
否则不要添加背景。
```

如果添加，必须说明理由，并保证背景不遮挡主体。

### 2.4 来自主 Agent 的背景上下文

主 Agent / Structure 在派发 Animation 时，传入预计动画位置的真实背景信息：

```json
{
  "background_context": {
    "position": {"start_sec": 12.0, "end_sec": 16.0},
    "frames": [
      {"time": 12.0, "path": "renders/bg/frame_1200.jpg"},
      {"time": 12.8, "path": "renders/bg/frame_1280.jpg"},
      {"time": 13.6, "path": "renders/bg/frame_1360.jpg"},
      {"time": 14.4, "path": "renders/bg/frame_1440.jpg"},
      {"time": 15.2, "path": "renders/bg/frame_1520.jpg"},
      {"time": 16.0, "path": "renders/bg/frame_1600.jpg"}
    ],
    "dominant_colors": ["#0B1220", "#1E293B"],
    "luminance": 0.08,
    "busyness": "low"
  }
}
```

动画 Agent 在决定 `font_color` / `background` 前：

```text
read_file(background_context.frames[*])
→ 看 5–6 张背景截图
→ 决定文字色/底板/背景
```

## 3. 工具契约

### workspace_tree

输入：

```json
{"root": "materials", "glob": "**/*.{png,jpg,webp}", "max_depth": 4}
```

输出：

```json
{"entries": [{"path": "...", "kind": "image", "bytes": 123456}]}
```

### read_file

输入：

```json
{"path": "materials/bg/keyboard.png", "mode": "image"}
```

输出：artifact ref + 元数据，不含二进制正文。

### background_frames

可选便捷工具：

```json
{"background_context": {...}} -> {"frames": [artifact refs]}
```

## 4. 验收

- Agent 能列工作区并选图；
- Agent 能读图并得到 artifact ref；
- LLM 明确选背景时，generator 不再强制清掉；
- 未明确选背景时仍默认透明；
- 动画颜色决策前能看到 5–6 张真实背景帧；
- 01 类“黑字叠黑底”不再发生；
- 所有选择有 `background_reason` 可追溯。
