"""text_effect_card plugin contribution."""
from __future__ import annotations

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from .card import TextEffectCardTool


class TextEffectCardPlugin(BasePlugin):
    id = "text_effect_card"

    def contributions(self):
        return [Contribution(ContributionKind.TOOL, "text_effect_card", TextEffectCardTool())]

    def initialize(self):
        return None
