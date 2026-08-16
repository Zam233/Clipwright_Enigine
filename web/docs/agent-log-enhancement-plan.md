> ⚠️ **Status: Pending** — this enhancement plan has not yet been implemented. It describes the intended design for Agent pipeline execution log UI in the AgentPanel.

# Agent 副驾驶 — 管线执行日志增强计划

## 一、背景与目标

当前 AgentPanel 的"生成管线"标签页仅显示：
- 阶段进度条（structure → material → edit → animation → audio → quality）
- 最终的时间线 diff 对比
- 错误信息

**缺失**的是 test-frontend 中"执行日志面板"提供的**完整工作流可见性**：
- LLM 每次调用的思考内容
- 使用到的工具参数与结果
- 匹配到的素材摘要
- Agent 启动/完成/失败状态
- 插件/技能调用记录
- 动画创建信息

目标：增强 AgentPanel 使其提供与 test-frontend 同等甚至更丰富的管线执行日志。

---

## 二、后端现有事件体系

`trace.py` 的 `add_event(pipeline_id, agent, event_type, summary, detail)` 产生以下事件结构：

```json
{
  "time": 1234567890.123,
  "agent": "structure_agent",
  "type": "llm|tool|skill|plugin|agent_start|agent_end|info|error",
  "summary": "简短的文字描述",
  "detail": { ... }   // 可选，结构化详情
}
```

**当前已监听的事件**（AgentPanel `openSSE`）：
| SSE event | 处理 |
|-----------|------|
| `agent_start` | 提取 `agent_name`，更新 phase |
| `agent_end` / `agent_complete` | 从 edit_agent 提取 timeline |
| `timeline_snapshot` | 更新 agentTimeline |
| `pipeline_complete` / `done` | 标记 completed |
| `agent_error` | 展示错误 |

**未监听但后端实际发送的事件**：
| SSE event | 包含内容 |
|-----------|----------|
| `tool` | `{ agent, type: "tool", summary: "🔧 tool_name(args)", detail: { tool, params } }` |
| `llm` | `{ agent, type: "llm", summary: "LLM(model) → content_preview", detail: ... }` |
| `skill` | `{ agent, type: "skill", summary: "...", detail: { skill, params } }` |
| `plugin` | `{ agent, type: "plugin", summary: "...", detail: { plugin } }` |
| `info` | `{ agent: "system", type: "info", summary: "加载 Persona: xxx" }` |
| `warning` | `{ agent, type: "warning", summary: "..." }` |

---

## 三、设计方案

### 3.1 面板布局调整

将 PipelineView 拆分为上下两个区域：

```
┌─ PipelineView ────────────────────────────┐
│ 上区（固定高度，可折叠）                    │
│   · 选题输入                                │
│   · 配音选项                                │
│   · 运行按钮                                │
│   · 阶段进度条                              │
│                                            │
│ ───────────────── 分隔线 ────────────────── │
│ 下区（flex-1，填充剩余空间）                 │
│   ╔═ 执行日志 ────── [清空] [N 条] ═╗     │
│   ║ ▶ structure_agent 启动              ║   │
│   ║   🤖 LLM(qwen-max) → 生成脚本...   ║   │
│   ║   🔧 tool_call(param=val)           ║   │
│   ║   ✓ structure_agent 完成            ║   │
│   ║ ▶ material_agent 启动              ║   │
│   ║   🧠 search_material(query=...)     ║   │
│   ║   📊 素材匹配: 3 条结果             ║   │
│   ╚═══════════════════════════════════════╝ │
└────────────────────────────────────────────┘
```

### 3.2 事件日志渲染规则

参考 test-frontend 的 `ewLog` 函数设计日志行：

| 事件类型 | 图标 | 颜色 | 示例 |
|---------|------|------|------|
| `agent_start` | ▶ | `text-primary` (蓝) | `▶ structure_agent 启动` |
| `agent_end` | ✓ | `text-track-audio` (绿) | `✓ structure_agent 完成 (12.3s)` |
| `llm` | 🤖 | `text-track-caption` (琥珀) | `🤖 qwen-max: "根据选题生成脚本结构..."` |
| `tool` | 🔧 | `text-on-surface-variant` (灰) | `🔧 search(keyword="散热设计")` |
| `skill` | 🧠 | `text-tertiary` (粉) | `🧠 dub_script: 3 段旁白` |
| `plugin` | 🔌 | `text-track-image` (紫) | `🔌 knowledge_longform` |
| `info` | ○ | `text-on-surface-variant/60` | `○ 加载 Persona: zam_knowledge` |
| `warning` | ⚠ | `text-track-text` (黄) | `⚠ 自动配音失败: provider timeout` |
| `error` | ✗ | `text-error` (红) | `✗ material_agent 失败: timeout` |
| `timeline_snapshot` | 📊 | `text-track-video` (蓝) | `📊 时间线更新: 5 轨, 240s` |

### 3.3 可展开详情

关键事件（tool / llm / skill）支持点击展开查看详情：

```
🤖 qwen-max: "根据选题生成脚本..."  [▸ 展开]
  ├─ System: 你是一个知识区视频脚本生成器...
  ├─ Prompt: 选题是"xxx"，请生成 5 段落脚本...
  └─ Response (320 tokens): 好的，以下是... (完整内容)
```

```diff
🔧 search(keyword="散热设计")  [▸ 展开]
  ├─ 参数: { query: "散热设计", limit: 5, source: "pexels" }
  └─ 结果: 3 条
       · video_001.mp4 (12s, 1920×1080)
       · video_002.mp4 (8s, 3840×2160)
       · video_003.mp4 (15s, 1280×720)
```

展开详情使用 `detail` 字段中的结构化数据（如果后端提供）。

### 3.4 Agent 分组视图

日志按 Agent 自动分组，每个 Agent 的日志段可折叠：

```
▸ structure_agent  ·  2 条日志  ·  12.3s
  ▶ structure_agent 启动
    🤖 qwen-max: "生成脚本..."
    ✓ structure_agent 完成 (12.3s)

▸ material_agent  ·  5 条日志  ·  8.1s
  ▶ material_agent 启动
    🧠 search: "散热设计"
  ...
```

分组折叠态默认展开当前活跃 Agent，其余折叠。

### 3.5 进度条与阶段联动

日志面板与现有的阶段进度条联动：
- `agent_start` → 对应 phase 进度条高亮
- `agent_end` → 对应 phase 打勾
- 每个 agent 的耗时显示在分组头部

### 3.6 离线模拟日志

离线模式下 `simulatePipeline` 也产生模拟日志行，保持行为一致。

---

## 四、实现步骤

### P1 — 日志数据层与 Store

**文件**: `src/stores/agentStore.ts`
- 新增 `LogEntry` 类型：`{ id, time, agent, type, summary, detail?, expanded? }`
- 新增 store 字段：`logEntries: LogEntry[]`
- 新增 actions：`addLogEntry(entry)`, `clearLogs()`, `toggleExpand(id)`

**文件**: `src/types/pipeline.ts`
- 新增 `LogEventType` 联合类型
- 新增 `LogEntry` 接口

### P2 — SSE 监听全覆盖

**文件**: `src/features/agent/AgentPanel.tsx` 的 `openSSE` 函数

新增监听器：
```ts
// 通用消息事件（SSE 的 onmessage 或 custom event）
es.addEventListener('message', handler) 或逐类型注册:
es.addEventListener('llm', handler)
es.addEventListener('tool', handler)
es.addEventListener('skill', handler)
es.addEventListener('info', handler)
es.addEventListener('warning', handler)
```

每个 handler 调用 `addLogEntry()` 写入 store。

**关键问题**：后端 SSE 的 event name 是 `tool`、`llm` 等，还是统一用 `message`？需确认 SSE stream 实现。

从 test-frontend 代码看，它使用 `evtSource.onmessage` 统一处理，然后按 `event.type` 分发。说明后端使用**无命名事件**，所有事件走 `message` 通道。

因此前端实现为：
```ts
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  const type = event.type;
  if (['agent_start','agent_end','agent_error'].includes(type)) continue; // 已有专门监听
  // 其余类型: llm, tool, skill, plugin, info, warning → addLogEntry
};
```

实际上后端同时使用了命名事件（`event: agent_start`）和通用消息（`event: message`）。需核实 SSE stream 的实现方式。最安全做法是**同时注册命名事件和 `onmessage` 兜底**。

### P3 — 日志面板 UI 组件

**文件**: `src/features/agent/AgentPanel.tsx` 新增 `LogPanel` 子组件

- 日志列表（虚拟滚动 React Virtual，考虑到可能有数百条日志）
- 每条日志：图标 + 时间戳 + agent 标签 + 内容 + 展开/折叠按钮
- 可展开详情区（JSON viewer 或格式化文本）
- 自动滚动到底部（新增日志时）
- 清空按钮
- 日志数量 badge
- Agent 分组折叠

### P4 — Agent 分组视图

- 按 `agent` 字段分组
- 计算每个 agent 的日志数、耗时
- 默认展开当前活跃 agent
- 折叠/展开切换

### P5 — 离线模拟补齐

`simulatePipeline` 中添加 `addLogEntry` 调用，产生模拟日志。

---

## 五、文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/types/pipeline.ts` | 修改 | 新增 `LogEntry`、`LogEventType` |
| `src/stores/agentStore.ts` | 修改 | 新增 logEntries 状态与 actions |
| `src/features/agent/AgentPanel.tsx` | 大幅修改 | SSE 全覆盖、LogPanel 组件、分组、布局调整 |

---

## 六、风险与注意事项

1. **SSE 消息通道确认**：需确认后端 SSE stream 使用的 event name 约定（命名事件 vs `message`），避免重复或遗漏
2. **日志内存**：长管线可能产生数百条日志。使用虚拟滚动（`@tanstack/react-virtual` 已在依赖中）+ 日志截断（最多保留 1000 条）
3. **布局空间**：当前 PipelineView 已经在侧面板中，空间紧张。需要合理的折叠/展开策略，默认展示最近 8-10 条日志，其余可滚动
4. **detail 字段格式**：后端 `detail` 是 `Any` 类型，可能是 dict/string/null。前端需做类型检测和兜底渲染
5. **与时间线 diff 共存**：完成后同时展示时间线 diff 和日志，需合理分配垂直空间
