"""text-motion-core —— 基础文字动效插件。"""

from __future__ import annotations

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from .providers.core import providers


class TextMotionCorePlugin(BasePlugin):
    id = "text-motion-core"

    def contributions(self) -> list[Contribution]:
        return [
            Contribution(ContributionKind.TEXT_MOTION, p.id, p,
                         meta={"preview": "exact", "fidelity": "exact"})
            for p in providers()
        ]

    def initialize(self) -> None:  # 旧轨兼容：不重复注册
        return None
