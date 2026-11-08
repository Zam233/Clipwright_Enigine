"""GenUI 展示插件：**能力主体**（契约 + 上限 + 卡片配方 + 教模型的规范）。

**它做什么**：向 engine 交付
1. 两条 `display_spec` 纯数据贡献（`brief` / `plan`）：组件白名单、容器/动作/色调/字号、
   9 个上限、`trust` 策略（brief=模型给了就洗、plan=模型的一律不看），以及（B3 起）
   卡片配方 `recipe`；
2. 一条 `PROMPT` 贡献：把"怎么写 spec"教给 `requirements` Agent。

**它不做什么**：

- 不渲染（渲染器是前端编译期产物 `src/features/genui/`）；
- 不提供校验器（engine 的通用清洗器 `services/display_spec/sanitizer.py` 按**本插件
  声明的契约**校验；信任边界不能外包给被约束方，见 PRD §3.3）；
- 不拥有权威数据：`CreativeBrief` 字段与规划书 `markdown_content` 才是确认与执行的对象。

**为什么契约写在这里**：白名单/上限/配方是"这类展示能力怎么工作"的知识，属于能力方。
放在 engine 就是一条 genui 专属常量表（PRD §3.2 判定 1/3 的反例）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind

#: 插件**自己**读自己的契约文件；engine 不读插件目录（PRD §3.1 N3：注册期零 I/O 指 engine 侧）。
CONTRACT: dict[str, Any] = json.loads(
    (Path(__file__).with_name("contract.json")).read_text(encoding="utf-8")
)

#: 卡片配方：**只给 engine**（不发给前端、不进模型提示词）。engine 的通用解释器
#: `services/display_spec/recipe.py`（B3）按 11 个固定算子执行它，插件不参与执行。
#: 注意：`plan` 的 `trust=recipe_only` 要求配方非空 —— 这是"规划书不能被展示层编造"
#: 的落地点（编码计划 §2.3）。
RECIPE: dict[str, Any] = json.loads(
    (Path(__file__).with_name("recipe.json")).read_text(encoding="utf-8")
)

#: 两条 `display_spec` 共用的契约字段（白名单/上限/枚举）。
_SHARED_KEYS = (
    "version", "components", "containers", "actions", "tones",
    "badge_tones", "text_sizes", "limits",
)


def _shared() -> dict[str, Any]:
    return {key: CONTRACT[key] for key in _SHARED_KEYS}


def _spec_meta(spec_id: str) -> dict[str, Any]:
    """一条 display_spec 贡献的 meta = 共享契约 + 该面的 spec 字段 + 配方。"""
    spec = dict(CONTRACT["specs"][spec_id])
    return {"spec_id": spec["spec_id"], **_shared(), **spec, "recipe": RECIPE[spec_id]}


_LIMITS = CONTRACT["limits"]
_COMPONENTS = "、".join(f"`{c}`" for c in CONTRACT["components"])

#: 规范正文。刻意写成"能做什么 + 不许做什么 + 一个最小示例"，不堆组件大全——
#: 模型只需要稳定产出合法 spec，不需要背文档。数字全部从契约插值，防文案与契约漂移。
PROMPT = f"""## GenUI 展示规范（来自 genui 插件）

除了 `brief_draft` 的既有字段，你可以**额外**给一个 `genui` 字段，用来把简报呈现成
结构化卡片（前端会渲染；不给时宿主会按插件配方从简报字段生成一份，所以**不要**为了它牺牲前者）。

规则（务必遵守，违反的部分会被后端丢弃）：

- `genui` 必须是 `{{"version": {CONTRACT["version"]}, "items": [ ... ]}}`；`items` 非空。
- 组件只允许这几种：{_COMPONENTS}。
- 容器型（`row` / `col` / `card` / `accordion`）用 `items` 放子节点；其余组件不接受子节点。
- 节点数 ≤ {_LIMITS["max_nodes"]}，嵌套 ≤ {_LIMITS["max_depth"]} 层；
  单个文本字段 ≤ {_LIMITS["max_text_chars"]} 字；
  表格 ≤ {_LIMITS["max_table_cols"]} 列 / {_LIMITS["max_array_items"]} 行。
- **不要**写 HTML / 脚本 / CSS / 外链图片 / URL / HTTP 方法；不需要 `action` 就不要写。
- 动作只允许 {"、".join(f"`{a}`" for a in CONTRACT["actions"])}，宿主**都已实现**，但各有各的闸：
  - `ask`：点击把 `payload.prompt` 发回当前需求会话（最安全，优先用它）；
  - `select`：必须给 `payload.options`（2–{_LIMITS["max_select_options"]} 个短选项、
    每个 ≤{_LIMITS["max_option_chars"]} 字），点了发「选择：<值>」——
    发送文案由宿主拼，**不要**把整句话写进选项；
  - `confirm`：会先弹二次确认再走卡片的确认流程，**只在确实建议用户直接确认当前简报/规划书时**用；
  - 只在真能给用户省事时才加动作——加一个点了没反应的按钮比不加更差。
- 示例：

```json
{{"version": {CONTRACT["version"]}, "title": "创意简报", "items": [
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
    """贡献 1 条提示词规范 + 2 条 `display_spec` 契约（全部声明式，无副作用）。"""

    def contributions(self) -> list[Any]:
        return [
            Contribution(
                ContributionKind.PROMPT,
                "genui_requirements_prompt",
                PROMPT,
                meta={"agent": "requirements", "prompt": PROMPT, "priority": 6},
            ),
            Contribution(
                ContributionKind.DISPLAY_SPEC, "brief", None, _spec_meta("brief"),
            ),
            Contribution(
                ContributionKind.DISPLAY_SPEC, "plan", None, _spec_meta("plan"),
            ),
        ]


__all__ = ["GenUIPlugin", "CONTRACT", "PROMPT"]
