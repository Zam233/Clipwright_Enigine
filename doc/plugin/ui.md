# 可视化视图：UI 槽位与布局 JSON

插件不需要写 React；用声明式 JSON 描述面板，宿主渲染。UI 有两种载体：

1. `ui_slot` 贡献：`meta.slot` + `meta.layout`，可挂到应用各区域；
2. 插件目录 `ui.json`：设置页/插件详情页的初始面板，`GET /api/plugin/{id}/ui` 读取。

## 1. 15 个槽位（名单冻结）

`nav.primary`、`nav.settings`、`page`、`editor.left-panel`、`editor.right-panel`、
`editor.bottom-panel`、`editor.toolbar`、`timeline.tool`、`timeline.context-menu`、
`props.section`、`export.preset`、`settings.section`、`create.dub`、`app.overlay`、`shortcut`。

权威名单在前端 `src/features/plugins/slots.ts`。新增 slot 必须走 SlotRegistry 评审。

```python
Contribution(
    ContributionKind.UI_SLOT, "my_panel", None,
    meta={
        "slot": "editor.right-panel",
        "title": "我的面板",
        "order": 150,
        "icon": "Puzzle",                       # 必须在前端 icon 白名单
        "visibleWhen": "context.hasTimeline === true",
        "layout": {"title": "我的面板", "widgets": [
            {"type": "input", "key": "endpoint", "label": "端点"},
            {"type": "button", "label": "测试", "action": {
                "endpoint": "/api/my/test", "method": "POST",
                "body": {"endpoint": "${endpoint}"}}},
        ]},
    },
)
```

## 2. 可见性表达式

`visibleWhen` 只允许「路径 + 比较」的受限表达式，禁止 `eval`。可用上下文
（`SlotVisibilityContext`）：

- `hasSelection`、`clipKind`、`trackKind`、`playing`、`hasTimeline`、`isAdmin`。

未知字段按 `undefined` 处理，不会把你整块隐藏之外的逻辑“猜”出来。

## 3. 布局 JSON

```json
{
  "title": "插件设置",
  "widgets": [
    {"type": "group", "title": "网络", "children": [
      {"type": "input", "key": "endpoint", "label": "端点"},
      {"type": "checkbox", "key": "enabled", "label": "启用", "defaultValue": true}
    ]},
    {"type": "row", "children": [
      {"type": "button", "label": "保存", "action": {"endpoint": "/api/my/save", "method": "POST"}},
      {"type": "spinner", "label": "加载中"}
    ]}
  ]
}
```

可用控件（前端 `src/features/plugins/types.ts`）：`textarea`、`input`（别名 `string`）、
`select`、`checkbox`、`slider`、`button`、`image`、`spinner`、`alert`、`text`、
`row`、`column`、`group`、`file`。

约定：

- `key` 是状态键；按钮 `action.body` 可用 `${key}` 插值；
- `select.optionsFrom` 只能指向宿主白名单数据源（当前支持 `voices`），插件拿不到任意数据；
- `file` 控件值是一个 `File` 对象，**上传由宿主执行**（`pluginApi` 不暴露给插件），
  插件只拿到上传回执；
- 未知 icon 会被丢弃；未知 widget 类型渲染成可读占位，不静默丢整块。

## 4. `ui.json` 与 bundle

- `plugins/<id>/ui.json`：`GET /api/plugin/{id}/ui` 返回；适合设置页单面板；
- 受信 bundle：`GET /api/plugin/{id}/bundle`，manifest 的 `bundle_sha256` 进入签名载荷，
  服务端读取时重算摘要；bundle 内不允许任意脚本/外链，安全评审按 `docs/genui.md` 的边界执行。

## 5. 前端开发注意

- 插件不能自己发请求到任意 endpoint；`button.action.endpoint` 必须走宿主允许的路径；
- 展示模型输出/接口字段时，前端一律走 `asText`/`textList`（运行时形状不可信）；
- 新增控件类型需要同时改前端渲染器与类型，并在文档中登记；否则插件声明了也只会显示占位。
