# 视觉 LLM 素材分析 — 前端集成文档

## 概述

后端已为素材库插件新增两个配置项，允许在 MaterialAgent 素材选择阶段启用视觉 LLM 帧分析：

- `enable_visual_llm`（bool）：开关，控制是否对候选素材进行视觉 LLM 分析
- `visual_llm_frame_count`（int）：每个候选素材抽取分析的帧数

前端需要在插件配置面板中暴露这两个字段的 UI 控件，使创作者可以在每个素材库插件中独立开关和配置此功能。

---

## 配置字段

| 字段 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `enable_visual_llm` | bool | `false` | — | 启用视觉 LLM 帧分析。开启后 MaterialAgent 会对每个候选素材抽取帧并调用多模态 LLM（Claude/GPT-4V/Qwen-VL）分析画面内容，替换当前的占位评分（固定 0.5） |
| `visual_llm_frame_count` | int | `3` | 1–5 | 每个候选素材分析几帧。帧数越多匹配越准，但耗时会线性增长（每帧约消耗一次 Vision API 调用 + 下载成本） |

两个字段在后端 config.yaml 中的存储格式（结构化字段）：

```yaml
fields:
  enable_visual_llm:
    type: bool
    value: false
    label: "启用视觉LLM帧分析"
    description: "开启后，MaterialAgent 会用视觉大模型分析候选素材的帧内容，提高匹配精度。"
  visual_llm_frame_count:
    type: int
    value: 3
    label: "分析帧数"
    description: "每个候选素材抽取分析的帧数（1-5）。更多帧 = 更准确，但更慢更贵。"
```

后端通过 `typed_config_to_values()` 将结构化字段转为扁平值，插件可通过 `self.config["enable_visual_llm"]` 访问。

---

## API 端点

前端通过以下已有端点完成配置的读取、保存和应用：

### 1. 获取插件列表（含配置）

```
GET /api/plugin/list
```

响应中包含每个插件的 `config` 字段。对于使用 `fields:` 格式的插件，`config` 为结构化对象：

```json
{
  "id": "pexels_material",
  "manifest": { "kind": "material", "name": "Pexels Material Library", ... },
  "config": {
    "fields": {
      "api_key": { "type": "string", "value": "", ... },
      "enable_visual_llm": { "type": "bool", "value": false, ... },
      "visual_llm_frame_count": { "type": "int", "value": 3, ... }
    }
  }
}
```

### 2. 获取插件结构化配置

```
GET /api/plugin/{plugin_id}/config
```

返回该插件的完整结构化配置。响应格式同上 `config` 字段。

### 3. 保存插件配置

```
PUT /api/plugin/{plugin_id}/config
Content-Type: application/json

{
  "fields": {
    "enable_visual_llm": { "type": "bool", "value": true },
    "visual_llm_frame_count": { "type": "int", "value": 5 }
  }
}
```

**注意**：PUT 请求的 body 支持增量更新 — 只需传入要修改的字段，后端会合并到现有配置中。传入完整的 `fields` 块可以覆盖全部字段。

### 4. 重载插件（应用新配置）

```
POST /api/plugin/{plugin_id}/reload
```

配置保存后调用此端点，后端会 `shutdown()` 旧实例、`initialize()` 新实例，使新配置生效。无需重启服务。

---

## 前端 UI 规范

### 适用范围

仅在 **素材库类插件**（`manifest.kind === "material"`）的配置面板中显示这两个字段。通过过滤 `/api/plugin/list` 返回的插件列表实现。

### 组件设计

```
┌─────────────────────────────────────────────┐
│ 视觉 LLM 分析                        [展开] │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────────────────────────────┐       │
│  │ 启用视觉LLM帧分析           [○] │       │
│  │ 开启后，MaterialAgent 会用视觉   │       │
│  │ 大模型分析候选素材的帧内容，提高  │       │
│  │ 匹配精度。                       │       │
│  └──────────────────────────────────┘       │
│                                             │
│  ┌──────────────────────────────────┐       │
│  │ 分析帧数                   [3] ▼│       │
│  │ 每个候选素材抽取分析的帧数       │       │
│  │（1-5）。更多帧 = 更准确，但更慢 │       │
│  │ 更贵。                          │       │
│  └──────────────────────────────────┘       │
│                                             │
│  [保存配置]                                 │
└─────────────────────────────────────────────┘
```

### 交互规则

1. **开关默认关闭**：`enable_visual_llm` 默认值为 `false`
2. **帧数输入联动**：`visual_llm_frame_count` 输入控件在开关关闭时应 `disabled` 状态；开关打开后变为可用
3. **帧数约束**：限制输入值在 1–5 范围内；建议使用 `<select>` 下拉或 `<input type="number" min=1 max=5>`
4. **操作提示**：开关旁边可加一个小型 tooltip/badge 提示"需要 Vision API 额度"；帧数 > 3 时显示橙色警告"高帧数会显著增加分析耗时"
5. **保存流程**：
   - 用户修改 → 点击"保存配置"按钮
   - 调用 `PUT /api/plugin/{plugin_id}/config`，body 中传入修改后的 fields
   - 保存成功后调用 `POST /api/plugin/{plugin_id}/reload`
   - 显示成功/失败 toast 通知

### 数据读取

从 `GET /api/plugin/{plugin_id}/config` 或 `/api/plugin/list` 中提取：

```typescript
interface VisualLLMConfig {
  enable_visual_llm: boolean;
  visual_llm_frame_count: number;
}

function extractVisualLLMConfig(config: PluginConfig): VisualLLMConfig {
  const fields = config?.fields ?? {};
  return {
    enable_visual_llm: fields.enable_visual_llm?.value ?? false,
    visual_llm_frame_count: fields.visual_llm_frame_count?.value ?? 3,
  };
}
```

### 数据写入

构造 PUT body 时：

```typescript
function buildConfigPayload(config: VisualLLMConfig) {
  return {
    fields: {
      enable_visual_llm: {
        type: "bool",
        value: config.enable_visual_llm,
      },
      visual_llm_frame_count: {
        type: "int",
        value: config.visual_llm_frame_count,
      },
    },
  };
}
```

---

## 后端行为

| 配置状态 | MaterialAgent 行为 |
|---------|-------------------|
| `enable_visual_llm = false`（默认） | 使用当前占位行为：帧验证返回固定 0.5 分（无实际视觉分析） |
| `enable_visual_llm = true` | 调用 VisionLLMTool：抽取帧 → VisionService 多模态分析 → 语义匹配评分（0–1）→ 分数参与素材排序 |

评分集成到现有排序公式：`visual_llm_score × 0.5 + 画面方向 × 0.25 + Persona 风格 × 0.25`

---

## 兼容性

- 如果插件没有这两个字段（旧版本 config.yaml），前端应 treat 为默认值（`false` / `3`）
- 后端代码完全向后兼容：`MaterialInput.material_plugin_config` 为可选字段，不存在时降级为默认行为
- 非素材库插件（kind ≠ "material"）不应在 UI 中显示此配置区块
