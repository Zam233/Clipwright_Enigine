# 插件 UI JSON 布局语言 (Plugin UI Layout Language)

## 概述

插件开发者使用声明式 JSON 描述前端界面，无需编写 React/TypeScript 代码。
后端在插件目录 `ui.json` 中定义布局，前端 `PluginLayoutRenderer` 引擎渲染为交互式 UI。

### 文件位置

```
plugins/{plugin_id}/
├── plugin.yaml          # 插件清单
├── main.py              # 插件逻辑
└── ui.json              # ★ 前端 UI 布局定义
```

### API

前端通过 `usePluginUI(pluginId)` Hook 获取布局，调用 `GET /api/plugin/{plugin_id}/ui`。

---

## 顶层结构

```json
{
  "title": "插件名称",
  "widgets": [ ... ]
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `title` | string | 否 | 插件标题（可选） |
| `widgets` | array | 是 | 顶层组件数组 |

---

## 组件类型

### 1. textarea — 多行文本输入

```json
{
  "type": "textarea",
  "key": "prompt",
  "label": "描述",
  "placeholder": "输入提示词…",
  "rows": 3,
  "defaultValue": ""
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `key` | string | 是 | 状态键，输入值绑定到 `state[key]` |
| `label` | string | 否 | 输入框标签 |
| `placeholder` | string | 否 | 占位文本 |
| `rows` | number | 否 | 行数（默认 3） |
| `defaultValue` | string | 否 | 默认值 |

值通过 `${key}` 语法在 button action 中引用。

### 2. button — 操作按钮

```json
{
  "type": "button",
  "label": "生成",
  "disabledWhen": "loading",
  "action": {
    "endpoint": "/api/tool/execute",
    "method": "POST",
    "loadingKey": "loading",
    "errorKey": "error",
    "successKey": "success",
    "body": {
      "tool": "my_tool",
      "params": {
        "prompt": "${prompt}",
        "width": 1024
      }
    },
    "resultMap": {
      "url": "url",
      "image_url": "data.image_url"
    }
  }
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `label` | string | 是 | 按钮文字 |
| `disabledWhen` | string | 否 | 当 `state[key]` 为 truthy 时禁用 |
| `action.endpoint` | string | 是 | API 路径（相对） |
| `action.method` | "POST" \| "GET" | 是 | HTTP 方法 |
| `action.loadingKey` | string | 否 | 请求中设为 `true`，完成设为 `false` |
| `action.errorKey` | string | 否 | 失败时存入错误消息 |
| `action.successKey` | string | 否 | 成功时设为 `true` |
| `action.body` | object | 否 | 请求体，支持 `${key}` 变量插值 |
| `action.resultMap` | object | 否 | 响应字段映射 `{ stateKey: "data.path" }` |

**变量插值**：`body` 中字符串值 `"${prompt}"` 会被替换为 `state["prompt"]`。
支持嵌套对象中的插值。

**响应处理**：若未指定 `resultMap`，响应 data 直接合并到 state。
通过 `resultMap` 可精确映射，如 `"data.image_url"` 提取嵌套字段。

### 3. image — 图片显示

```json
{
  "type": "image",
  "sourceField": "url",
  "alt": "生成结果",
  "visibleWhen": "url"
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `sourceField` | string | 是 | 图片 URL 对应的 state 键 |
| `alt` | string | 否 | 图片 alt 文本 |

### 4. spinner — 加载指示器

```json
{
  "type": "spinner",
  "label": "生成中…",
  "visibleWhen": "loading"
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `label` | string | 否 | 加载提示文字 |

### 5. alert — 消息提示

```json
{
  "type": "alert",
  "severity": "error",
  "textField": "error",
  "visibleWhen": "error"
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `severity` | "error" \| "info" \| "success" | 是 | 消息级别 |
| `textField` | string | 是 | 消息文本的 state 键 |

### 6. text — 文本展示

```json
{
  "type": "text",
  "content": "默认文本",
  "contentField": "status",
  "size": "caption"
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `content` | string | 否 | 静态文本（兜底） |
| `contentField` | string | 否 | 动态文本的 state 键 |
| `size` | "caption" \| "body" | 否 | 文本大小 |

### 7. row — 水平排列

```json
{
  "type": "row",
  "gap": 2,
  "children": [ ... ]
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `children` | array | 是 | 子组件列表 |
| `gap` | number | 否 | 间距（×4px，默认 2=8px） |

### 8. column — 垂直排列

```json
{
  "type": "column",
  "gap": 3,
  "children": [ ... ]
}
```

属性同 `row`。默认间距 12px。

### 9. group — 分组容器

```json
{
  "type": "group",
  "title": "配置",
  "children": [ ... ]
}
```

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `title` | string | 否 | 分组标题 |
| `children` | array | 是 | 子组件列表 |

---

## 通用属性

所有组件支持以下属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| `key` | string | 组件唯一标识（textarea 必需） |
| `visibleWhen` | string | 条件渲染：`state[key]` 为 truthy 时显示 |

---

## 完整示例

```json
{
  "title": "AI 图片生成",
  "widgets": [
    {
      "type": "group",
      "title": "文生图",
      "children": [
        {
          "type": "textarea",
          "key": "prompt",
          "label": "描述",
          "placeholder": "描述你想生成的图片…",
          "rows": 3
        },
        {
          "type": "row",
          "gap": 2,
          "children": [
            {
              "type": "button",
              "label": "生成",
              "disabledWhen": "loading",
              "action": {
                "endpoint": "/api/tool/execute",
                "method": "POST",
                "loadingKey": "loading",
                "errorKey": "error",
                "body": {
                  "tool": "my_image_gen",
                  "params": { "prompt": "${prompt}" }
                },
                "resultMap": { "url": "url" }
              }
            }
          ]
        },
        { "type": "spinner", "label": "生成中…", "visibleWhen": "loading" },
        { "type": "alert", "severity": "error", "textField": "error" },
        { "type": "image", "sourceField": "url", "alt": "结果", "visibleWhen": "url" }
      ]
    }
  ]
}
```

---

## 开发流程

1. 在 `plugins/{plugin_id}/` 创建 `ui.json`
2. 定义组件树和交互逻辑
3. 启动后端 → 前端加载插件 → PluginPanel 自动渲染
4. 修改 `ui.json` 后热更新（重新点击 tab 即可刷新）
5. 无需重启或重新编译前端代码
