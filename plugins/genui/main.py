"""GenUI 展示插件：给需求 Agent 补一段「怎么产出 GenUI spec」的提示词。

**它做什么**：向 `requirements` Agent 的提示词追加一条**展示规范**，告诉模型：
创意简报（以及需要时的规划书说明）可以额外给一份 `genui` 字段，用一份受控 JSON
描述"这张卡片长什么样"。

**它不做什么**：

- 不渲染、不校验（渲染在前端 `src/features/genui/`，校验在后端
  `clipwright/schema/genui.py::sanitize_spec`）——插件只说"能写什么"；
- 不拥有权威数据：`CreativeBrief` 字段与规划书 `markdown_content` 才是确认与执行的对象，
  spec 丢了/坏了业务照常（宿主会用确定性构造器补一份）；
- 不新增贡献类型、不注册 slot、不覆盖内置提示词（`override_builtin: false`）。

**为什么规范写在插件而不是 `prompts.py`**：这是"某类能力怎么用"的说明，属于能力方
（GenUI）的知识；换实现/加组件时只改插件，不动全局提示词单一来源（不变量 #23）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind

#: 组件白名单（必须与 `clipwright/schema/genui.py::GENUI_COMPONENT_TYPES` 一致；
#: `tests/clipwright/test_genui_spec.py` 有一条断言盯着这个一致性）。
COMPONENTS = (
    "text", "list", "callout", "badge", "stat", "progress", "table",
    "steps", "divider", "row", "col", "card", "accordion",
)

#: 规范正文。刻意写成"能做什么 + 不许做什么 + 一个最小示例"，不堆组件大全——
#: 模型只需要稳定产出合法 spec，不需要背文档。
PROMPT = f"""## GenUI 展示规范（来自 genui 插件）

除了 `brief_draft` 的既有字段，你可以**额外**给一个 `genui` 字段，用来把简报呈现成
结构化卡片（前端会渲染；不给时宿主会从简报字段自动生成一份，所以**不要**为了它牺牲前者）。

规则（务必遵守，违反的部分会被后端丢弃）：

- `genui` 必须是 `{{"version": 1, "items": [ ... ]}}`；`items` 非空。
- 组件只允许这几种：{"、".join(COMPONENTS)}。
- 容器型（`row` / `col` / `card` / `accordion`）用 `items` 放子节点；其余组件不接受子节点。
- 节点数 ≤ 100，嵌套 ≤ 6 层；单个文本字段 ≤ 4000 字；表格 ≤ 12 列 / 100 行。
- **不要**写 HTML / 脚本 / CSS / 外链图片 / URL / HTTP 方法；不需要 `action` 就不要写。
- 示例：

```json
{{"version": 1, "title": "创意简报", "items": [
  {{"type": "text", "content": "一句话概述", "size": "h2"}},
  {{"type": "row", "items": [
    {{"type": "stat", "label": "预估时长", "value": "90s"}},
    {{"type": "badge", "label": "知识区", "tone": "accent"}}
  ]}},
  {{"type": "table", "columns": ["维度", "内容"], "rows": [["目标受众", "…"]]}},
  {{"type": "callout", "tone": "warning", "title": "待确认", "content": "…"}}
]}}
```

内容建议：用 `text` 点题、`table` 摆字段、`list` 列关键元素、`callout` 标红线/待确认。
**它是展示层**：字段本身（标题/受众/时长…）仍要按字段表完整给出。"""


class GenUIPlugin(CapabilityPlugin):
    """贡献一条 `requirements` 提示词扩展（声明式，无副作用）。"""

    def contributions(self) -> list[Any]:
        return [
            Contribution(
                ContributionKind.PROMPT,
                "genui_requirements_prompt",
                PROMPT,
                meta={"agent": "requirements", "prompt": PROMPT, "priority": 6},
            ),
        ]


__all__ = ["GenUIPlugin"]
