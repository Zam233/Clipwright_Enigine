"""web_media 插件 —— 把「从网页拿图」与「判断哪些场景需要外部事实」做成可替换能力。

用户 2026-11-02 裁定：`web_search` / `web_fetch` 既然是插件提供的，那么
`web_media` / `web_images` / `fact_intent` 也必须是插件，不能躺在 `clipwright/services`
里（否则它们就是"半个内置"，插件体系对这两个能力失灵）。

本插件贡献两个工具（都是语义能力，agent 只按能力名调用）：

| 工具 | 能力名 | 干什么 | 权限档位 |
|---|---|---|---|
| `web_images` | `web_images` | 抓网页 → 抽真图 → 落 `materials/web/` | `costly` |
| `fact_need` | `fact_need` | 一次批量模型判断"哪些场景要外部事实" | `costly` |

配置（`config.yaml`）：
- `fact_judge`：`llm`（默认）｜`heuristic`｜`off`，缺省时读全局
  `animation_fact_judge`，插件配置优先。
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind

from .fact_need import FactNeedTool
from .web_images import WebImagesTool


class WebMediaPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="web_media",
        name="网页媒体与事实判断",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description=(
            "从网页抽取真实图片并落盘（能力 web_images）+ 判断哪些场景需要外部事实"
            "（能力 fact_need，模型判断、一次批量）"
        ),
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        return [
            Contribution(ContributionKind.TOOL, "web_images", WebImagesTool()),
            Contribution(ContributionKind.TOOL, "fact_need", FactNeedTool()),
        ]

    def shutdown(self) -> None:
        pass


__all__ = ["WebMediaPlugin"]
