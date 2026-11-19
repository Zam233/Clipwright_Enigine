# 动画背景与工作区素材 Coding Plan

关联 PRD：`docs/animation-background-workspace-prd.md`

## B0 契约

新增：

```text
clipwright/animation/agentic/workspace_tools.py
clipwright/animation/agentic/background_context.py
```

类型：

```python
WorkspaceEntry(path, kind, bytes, mime, width, height)
BackgroundContext(position, frames, dominant_colors, luminance, busyness)
BackgroundDecision(mode, value, reason)
```

## B1 workspace_tree

实现：

- 限制 root 在 workspace 内；
- 拒绝 `..` / 绝对路径逃逸；
- glob 只读；
- 返回相对路径 + kind + bytes；
- 不读文件正文。

测试：

- 列目录；
- 逃逸拒绝；
- 空目录；
- 非图片过滤。

## B2 read_file

实现：

- text：返回 UTF-8 文本，超长写 artifact；
- image：返回 artifact_ref + 尺寸 + mime；
- binary：只返回 artifact_ref，不进上下文；
- 不执行任何文件内容。

测试：

- 图片尺寸；
- 文本截断；
- 路径逃逸；
- 缺失文件。

## B3 generate_mg 背景参数

`agent_tool_impl.generate_mg` 增加：

```python
background_mode: str = "transparent"
background_value: str = ""
background_reason: str = ""
```

传给 `MGGenerator.generate(... allow_opaque_background=...)`。

`MGGenerator._ensure_no_background` 增加：

```python
allow_opaque_background: bool = False,
background_reason: str = "",
```

保留条件：

```python
if allow_opaque_background:
    return mg_def
```

否则旧逻辑。

## B4 提示词

`ANIMATION_PROMPT_CORE` 增加：

```text
默认 transparent。
只有用户/需求/结构/主 Agent 明确要求，或背景能改善可读性时才加背景。
若加背景，必须给 background_reason。
若 transparent，文字颜色必须能覆盖在真实背景帧上。
```

## B5 背景上下文

`AnimationAgent.execute` 读取：

```python
input_data.background_context
```

存到：

```text
self._background_context
```

生成 prompt 注入：

```text
预期背景帧：6 张
dominant_colors/luminance/busyness
```

图片帧通过 `read_file` 工具暴露给 Agent。

## B6 Agent 决策顺序

系统提示词：

```text
决定 font_color / background 前：
1. read_file 背景帧；
2. 判断实际亮度/忙碌度；
3. 选择透明/色块/图片背景；
4. 给出 background_reason。
```

## B7 Final 证据

`final` 检查：

```text
background_mode 与 mg_def.style.background 一致
若 background_mode=color/image，必须有 background_reason
若 transparent，inspect_frames 必须基于真实背景帧，而不是纯黑底
```

## B8 测试

- 透明 + 浅色字通过；
- 透明 + 深色字在深背景帧上失败；
- LLM 显式背景保留；
- 未显式背景被清除；
- 工作区选图进入 image 元素；
- 路径逃逸拒绝；
- 5–6 背景帧进入 prompt/artifact。

## 文件

```text
clipwright/animation/agentic/workspace_tools.py
clipwright/animation/agentic/background_context.py
clipwright/animation/agent_tool_impl.py
clipwright/animation/agent_tools.py
clipwright/animation/mg/generator.py
clipwright/agents/animation_agent.py
clipwright/prompts/engine/animation_prompt.py
tests/clipwright/test_workspace_tools.py
tests/clipwright/test_background_context.py
```
