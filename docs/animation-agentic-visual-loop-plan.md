# 动画 Agentic 视觉质检与自修复方案

- 状态：Plan
- 目标：把“生成 → 渲染 → 看帧 → 判断 → 修改”做成真正的 Agent Loop
- 明确不做：pipeline 式点对点修 `font_color`、单位规则、bbox 规则

## 1. 问题定义

上一版仍然是 pipeline 思维：

```text
生成
→ semantic_layout_errors
→ 硬编码规则
→ 点对点 repair font_color / 单位 / y
```

这不符合 agentic：

- 规则是写死的；
- 失败是工具替 Agent 判断的；
- Agent 没有看到画面；
- 修的是字段，不是“动画看起来有什么问题”。

正确方式：

```text
Agent 生成
→ 渲染预览
→ 抽帧
→ 调 vision LLM 看帧
→ 得到“哪里看起来不对”的观察
→ Agent 自己决定改什么
→ revise_mg
→ 再渲染/再看
```

## 2. 核心原则

1. **判断交给模型，能力交给 Tool**  
   不在 Agent 里写“120|% 必须合并”“#12161C 必须改浅色”。
2. **视觉问题必须用视觉证据判断**  
   颜色可读性、层级、拥挤、标签错位由 vision LLM 看帧给出。
3. **规则只保留硬契约**  
   schema、字段类型、越界、非法属性；审美/构图/可读性不进硬编码规则。
4. **Agent 必须看到失败**  
   vision finding 是 Observation，不是日志。
5. **修复是 Agent 的动作**  
   `revise_mg` 接收 mg_def + findings + frame refs；不是 `font_color=...`。
6. **final 需要证据**  
   最终 mg_def 必须对应一次已渲染、已看帧、未被 blocking finding 的 artifact。

## 3. Tool Surface

### `generate_mg`
输入：brief / description / text_content / style。  
输出：mg_def + artifact_ref；不做视觉判断。

### `preview_html`
输入：mg_def、size。  
输出：合成 HTML + timeline runtime ref。  
不渲染 MP4，不启动 HyperFrames；只在隔离浏览器里播放。

### `capture_preview_frames`
输入：preview HTML、时间点（0/25/50/75/100% 或 Agent 指定）。  
输出：frame artifact refs。  
实现：headless Chrome 打开预览 iframe，调用 `window.__timelines["main"].seek(t)`，截图。  
**不调用完整 render**，毫秒级抽帧。

### `inspect_frames`
输入：frame refs / mp4、问题提示、可选的关注点。  
输出：结构化 findings：

```json
{
  "status": "issues",
  "findings": [
    {
      "frame": 0.25,
      "severity": "blocking",
      "category": "legibility",
      "observation": "数字 120% 在黑色背景上几乎不可见",
      "affected_region": {"x": ..., "y": ..., "w": ..., "h": ...},
      "evidence_ref": "frame_025.png"
    }
  ]
}
```

这是 vision LLM 的判断，不是规则引擎。

### `render_final`
只有 `inspect_frames` 通过后才调用，渲染 MP4 成片。  
生成阶段禁止每轮 render_final。

### `revise_mg`
输入：mg_def、findings、frame refs、原始 brief。  
输出：新 mg_def；必须说明改了哪些视觉问题。

### `validate_schema`
只做硬契约：字段类型、keyframes 时间、非法属性、越界。  
不做审美/构图判断。

### `ask_user`
主观取舍（风格、文案、是否接受）时询问。

### `final`
必须带：最终 mg_def、render artifact、inspect artifact、未解决 findings。

## 4. Agent Loop

```text
1. generate_mg
2. preview_html            # 不渲染 MP4
3. capture_preview_frames  # 浏览器 seek + 截图
4. inspect_frames          # vision LLM 看帧
5. if findings blocking:
       revise_mg(mg_def, findings, frames)
       → preview_html
       → capture_preview_frames
       → inspect_frames
   else:
       render_final         # 只在这里出 MP4
       final
```

硬门禁只有一条：

```text
final 的 mg_def hash 必须等于最后一次 render 的 mg_def hash
且最后一次 inspect 没有 blocking findings
```

不允许：

```text
生成后直接 final
只看 schema 就 final
工具偷偷改字段后 success
```

## 5. Prompt 设计

系统提示词只写流程与判断标准，不写点对点字段修复：

```text
你是动画导演 Agent。

每次生成 mg_def 后，必须：
1. 调 render_preview；
2. 调 inspect_frames 看 0/25/50/75/100% 帧；
3. 把 findings 当作你的观察；
4. 如果存在 blocking finding，调 revise_mg；
5. revise 后必须重新 render + inspect；
6. 只有 inspect 通过，才能 final。

检查重点（由你判断，不是规则表）：
- 文字是否可读、对比是否足够；
- 元素是否重叠、出画、贴边；
- 信息层级是否清楚；
- 数字/单位/标签是否语义完整；
- 动画是否有明确主运动；
- 画面是否像专业动效，而不是模板。
```

提示词不再写：

```text
120|% 必须合并
font_color 必须是 #E8F0F7
y 必须 >= font_size*1.4
```

这些是模型看帧后自己判断。

## 6. 数据与证据

```text
AnimationArtifact
  mg_def_hash
  preview_mp4
  frames[]
  inspect_report
  findings[]
  revisions[]
```

每次 revise 追加 revision，旧 artifact 保留。

Agent 的 observation 包含：

```text
artifact_ref
findings
frame_refs
```

不把整段 HTML 塞进上下文。

## 7. Final Gate

`final` 工具参数必须包含：

```json
{
  "mg_def_hash": "...",
  "render_ref": "...",
  "inspect_ref": "...",
  "unresolved_findings": []
}
```

校验：

```text
hash 匹配
render 存在
inspect 存在
unresolved_findings 为空
```

否则 `final` 被拒绝，回到 loop。

## 8. 视觉工具实现

`inspect_frames` 调 vision LLM：

- 输入：帧图 + 当前 mg_def 摘要 + brief；
- 输出 JSON findings；
- 不预设“颜色阈值”“字号阈值”；
- 可以给检查维度，但不给硬编码阈值；
- 失败时返回 `inspect_failed`，Agent 可重试。

Vision 模型选择由配置决定，不在 Agent 内写死。

## 9. 删掉什么

- 删除 `MG_TEXT_LOW_CONTRAST` 这类硬编码审美门禁；
- 删除 `120|%` 单位规则；
- 删除流程安全区硬编码；
- 删除 `repair_mg` 里点对点 `font_color`；
- 保留 schema/非法字段/越界等硬契约；
- 保留 build/render/inspect 的 artifact。

## 10. 02 的正确修复路径

当前：

```text
120% 仍 #12161C
→ 硬编码检测
→ 让 repair 改 font_color
```

新路径：

```text
generate_mg: 120% + 柱
render_preview
inspect_frames:
  "数字和标签在黑色背景上不可见"
revise_mg(mg_def, findings, frames):
  改文字颜色/加底板/换构图
render_preview
inspect_frames: pass
final
```

Agent 可以选：

- 改浅色；
- 加半透明底板；
- 改背景；
- 换布局。

不是被工具强迫改 `font_color`。

## 11. 实施顺序

```text
V0  contracts: Artifact / Finding / InspectReport
V1  render_preview + frames
V2  inspect_frames(vision LLM)
V3  revise_mg
V4  loop + final gate
V5  remove aesthetic hardcodes
V6  02/03/04/05 逐帧回归
V7  DeepSeek 5 个 + 渲染
```

## 12. 验收

- Agent 每次生成后都 render + inspect；
- findings 来自帧，不是规则；
- 02 的修复由 Agent 看帧后决定；
- 没有 `font_color` 点对点修复；
- 没有单位/字号硬编码；
- final 必须有 render/inspect 证据；
- 5 个样片可交付。
