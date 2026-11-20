# 插件配置

插件配置分两层：源码里的默认值 + 运行时覆盖值。插件通过 `self.config` 读取合并后的扁平键值。

```
plugins/<id>/config.yaml                       # 默认值（只读）
PluginData/plugins/<id>/config.yaml            # 运行时覆盖（管理页写入）
```

## 1. 结构化 fields

```yaml
fields:
  api_key:
    type: string
    value: ""
    label: "API Key"
    description: "从服务商控制台获取"
    secret: true
  max_results:
    type: int
    value: 10
    label: "最大结果数"
  enabled:
    type: bool
    value: true
    label: "启用"
```

支持类型：`string / int / float / bool / dict / list`。`secret: true` 的字符串字段：

- 落盘前用 Fernet 加密（`plugins/config_types.py`）；
- 读取接口返回掩码（保留前 4 字符）；
- `PUT` 时把掩码原样回传 = **保留旧值**，不会把密钥改成字面量 `****`。

## 2. 合并规则

- 默认值来自 `plugins/<id>/config.yaml`；
- 覆盖值来自 `PluginData/plugins/<id>/config.yaml`；
- 同格式时按 `fields` 逐字段合并；异格式时提取扁平值后合并；
- `self.config` 是扁平字典，例如 `self.config["api_key"]`；
- 配置版本 `config_schema_version` + 插件目录可选 `migrations.py` 做升级迁移。

## 3. 配置 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/plugin/{id}/config` | 读合并配置（secret 掩码） |
| PUT | `/api/plugin/{id}/config` | 写覆盖值（掩码回传=保留） |
| DELETE | `/api/plugin/{id}/config` | 清覆盖，回到源码默认 |

前端「插件详情 → 配置」直接消费这些接口；插件不需要自己写前端表单。

## 4. Provider 的 config_schema

Provider 类贡献（LLM/TTS/STT/Image/Embedding/Rerank/Renderer）除了 `config.yaml`，还要在
manifest 的贡献声明里给 `config_schema`，前端设置页据此渲染表单：

```yaml
contributions:
  - kind: llm_provider
    id: my-llm
    config_schema:
      base_url: {type: string, label: "Base URL", default: "https://api.example.com/v1"}
      api_key: {type: string, label: "API Key", secret: true}
      model: {type: string, label: "默认模型", default: "my-model"}
    capabilities:
      models: ["my-model"]
      stream: true
      tools: true
      usage: true
```

- `secret: true` 的字段与插件配置同一套加密/掩码规则；
- `capabilities` 是**能力声明**：未声明即视为不支持，运行时不得假装支持；
- 未知控件类型/未知能力键必须显式提示，不能静默丢弃。

## 5. 排错

- 配置写了但读不到：确认键在 `fields` 下、且 `value` 存在；
- 保存后没生效：插件需要重载（`POST /api/plugin/load/{id}`）；
- `config_schema` 不显示：检查 `api_version: "2"` 与贡献声明是否在 manifest 中；
- 密钥被写成明文：确认字段声明了 `secret: true`，且通过 `/config` 接口回写。
