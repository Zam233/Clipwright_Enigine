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
        from clipwright.animation.text_specs import TEXT_ANIM_SPECS
        builtin = [
            Contribution(ContributionKind.TEXT_PROMPT_PACK, spec.id, None, meta={
                "name": spec.name, "intent": spec.intent, "motion": spec.motion,
                "constraints": list(spec.constraints),
                "anti_patterns": list(spec.anti_patterns),
                "default_anchor": spec.default_anchor,
                "prompt": "",
            })
            for spec in TEXT_ANIM_SPECS
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
