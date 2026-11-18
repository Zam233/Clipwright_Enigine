"""text-motion-llm plugin entry."""
from __future__ import annotations

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from .author import TextMotionAuthor


class TextMotionLlmPlugin(BasePlugin):
    id = "text_motion_llm"

    def contributions(self):
        return [Contribution(ContributionKind.TEXT_MOTION, "llm_motion", TextMotionAuthor(),
                             meta={"preview": "exact", "fidelity": "exact"})]

    def initialize(self):
        return None
