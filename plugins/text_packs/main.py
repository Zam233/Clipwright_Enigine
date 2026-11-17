"""Prompt / style / asset packs example (data-only)."""
from __future__ import annotations

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind

_PROMPT = """## Intent
把引语做成一块有来源感的强调文字
## Semantic constraints
只引用旁白里出现过的话，不伪造
## Composition
单行或两行，居中，避开底部字幕带
## Motion
短促浮入，末段静止
## Anti-patterns
不要长引用、不要遮挡人脸
## Example
{"text_animation":{"style":{"font_size":72,"fill":"#ffffff"}}}
## Style reference
编辑部深色卡片
"""


class TextPacksPlugin(BasePlugin):
    id = "text_packs"

    def contributions(self):
        import json
        from pathlib import Path
        pack = json.loads((Path(__file__).with_name("builtin_types.json")).read_text(encoding="utf-8"))
        builtin = [
            Contribution(ContributionKind.TEXT_PROMPT_PACK, item["id"], None, meta={
                "name": item.get("name") or item["id"], "intent": item.get("intent") or "",
                "motion": item.get("motion") or "",
                "constraints": item.get("constraints") or [],
                "anti_patterns": item.get("anti_patterns") or [],
                "default_anchor": item.get("default_anchor") or "center",
                "prompt": item.get("prompt") or "",
            })
            for item in pack.get("types") or []
        ]
        return [
            Contribution(ContributionKind.TEXT_PROMPT_PACK, "spotlight_quote", None, meta={
                "name": "聚光引语", "intent": "引语强调", "motion": "fade_in",
                "constraints": ["不伪造引语"], "anti_patterns": ["长引用"],
                "prompt": _PROMPT, "version": "2026-11-17",
            }),
            Contribution(ContributionKind.TEXT_STYLE_PACK, "editorial_dark", None, meta={
                "name": "编辑部深色", "style": {"font_size": 72, "fill": "#ffffff",
                                                "box": True, "box_color": "#12121a99",
                                                "shadow_blur": 14, "shadow_y": 5},
            }),
            Contribution(ContributionKind.TEXT_ASSET_PACK, "paper_assets", None, meta={
                "name": "纸张资产", "assets": [{"id": "paper_01", "kind": "texture",
                                              "path": "plugins/text_packs/assets/paper_01.png",
                                              "license": "CC0"}],
            }),
            *builtin,
        ]

    def initialize(self):
        return None
