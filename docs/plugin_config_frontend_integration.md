# 插件配置管理 — 前端集成指南

> 本文档供前端开发者使用，说明如何通过 API 读取和编辑插件的运行时配置。

---

## 配置格式：结构化字段声明

每个插件的 config.yaml 使用**结构化 fields 格式**，每个字段声明类型、值和标签：

```yaml
fields:
  api_key:
    type: string
    value: ""
    label: "Pexels API Key"
    description: "从 https://www.pexels.com/api/ 获取"
  max_results:
    type: int
    value: 10
    label: "最大结果数"
  enabled:
    type: bool
    value: true
    label: "启用"
```

配置文件位置：
- 源码默认值：`plugins/{plugin_id}/config.yaml`
- 运行时覆盖值：`PluginData/plugins/{plugin_id}/config.yaml`（前端通过 API 编辑）

---

## 类型枚举与前端控件映射

| type | Python 类型 | 前端建议控件 | 示例 value |
|------|-----------|-------------|-----------|
| `string` | `str` | 文本框 / 密码框 | `"sk-xxx"` |
| `int` | `int` | 数字输入（整数） | `10` |
| `float` | `float` / `int` | 数字输入（小数） | `0.8` |
| `bool` | `bool` | 开关 / 复选框 | `true` |
| `dict` | `dict` | JSON 编辑区 | `{"a": 1}` |
| `list` | `list` | JSON 编辑区 / 标签输入 | `["a", "b"]` |

---

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/plugin/list` | GET | 列出所有已加载插件 |
| `/api/plugin/{plugin_id}/config` | GET | 读取结构化配置（含 type/value/label） |
| `/api/plugin/{plugin_id}/config` | PUT | 写入配置覆盖（YAML），校验 type 与 value 匹配 |
| `/api/plugin/{plugin_id}/config` | DELETE | 删除运行时覆盖，回退到源码默认值 |

Base URL: `http://localhost:8000`

---

## 1. 读取插件配置

```bash
curl http://localhost:8000/api/plugin/pexels_material/config
```

响应（结构化 JSON）：

```json
{
  "fields": {
    "api_key": {
      "type": "string",
      "value": "",
      "label": "Pexels API Key",
      "description": "从 https://www.pexels.com/api/ 获取，免费版每小时 200 次请求"
    }
  }
}
```

前端可根据 `fields[field_name].type` 决定渲染哪种表单控件，`value` 填充初始值，`label` 作为字段标签。

---

## 2. 写入配置

请求体为 **YAML**（`Content-Type: text/plain`），必须包含 `fields` 键：

```bash
curl -X PUT http://localhost:8000/api/plugin/pexels_material/config \
  -H "Content-Type: text/plain" \
  -d 'fields:
  api_key:
    type: string
    value: "sk-your-key-here"
    label: "Pexels API Key"'
```

成功响应：
```json
{ "status": "ok", "plugin_id": "pexels_material" }
```

### 类型校验

PUT 时会校验每个字段的 `value` 是否与 `type` 匹配：

- `"count": {"type": "int", "value": "not_a_number"}` → 400
- `"flag": {"type": "bool", "value": "yes"}` → ⚠️ 注意：YAML 中 `yes` 解析为 `True`（布尔），可正常通过
- `"tags": {"type": "list", "value": "not_list"}` → 400

类型不匹配时返回：
```json
{
  "detail": {
    "message": "配置校验失败",
    "errors": ["字段 'count' value 类型应为 int，实际为 str"]
  }
}
```

---

## 3. 删除配置覆盖

```bash
curl -X DELETE http://localhost:8000/api/plugin/pexels_material/config
```

删除 `PluginData/plugins/{plugin_id}/config.yaml`，插件回退到源码默认配置。

---

## 4. 错误码

| 状态码 | 含义 | 前端动作 |
|--------|------|----------|
| 200 | 成功 | 展示/更新 |
| 400 | YAML 格式错误 / 类型不匹配 / 缺少 fields 键 | 展示 errors 列表 |
| 404 | 插件未加载 | 刷新插件列表 |
| 503 | 插件系统未初始化 | 等服务启动 |

---

## 5. 典型工作流

```
1. GET /api/plugin/list                      → 获取可用插件
2. GET /api/plugin/{id}/config               → 获取结构化配置（含 type/value/label）
3. 前端根据 type 渲染表单：
   - string → <input type="text">
   - int/float → <input type="number">
   - bool → <input type="checkbox">
   - dict/list → <textarea>（JSON 编辑）
4. 用户编辑后，构造 YAML（保留 fields 结构和 type/label，仅修改 value）
5. PUT /api/plugin/{id}/config               → 提交 YAML
6. 如需回退：DELETE /api/plugin/{id}/config
```

---

## 注意事项

- **YAML 格式**：PUT 请求体必须是合法 YAML mapping（含 `fields` 键），不是 JSON
- **保留 type/label**：写入时每个字段需保留 `type` 和 `label`，仅修改 `value`
- **YAML 布尔陷阱**：`yes`/`no`/`true`/`false`/`on`/`off` 会被解析为布尔值。如需字符串 `"yes"`，使用引号
- **配置即时生效**：写入后插件立即可通过 `self.config` 读取新值（flat dict 格式，向后兼容）
- **不覆盖源码**：只写 `PluginData/` 目录，不改源码

