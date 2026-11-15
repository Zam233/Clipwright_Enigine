"""Agent 编排层 — 阶段子 Agent。

每个 Agent 负责一个阶段的子任务，由主 Agent 通过 `delegate_agent` 按需派发。
"""

from .base import BaseAgent
from .requirements_agent import RequirementsAgent
from .structure_agent import StructureAgent
from .material_agent import MaterialAgent
from .edit_agent import EditAgent
from .animation_agent import AnimationAgent
from .audio_agent import AudioAgent
from .quality_agent import QualityAgent

__all__ = [
    "BaseAgent",
    "RequirementsAgent",
    "StructureAgent",
    "MaterialAgent",
    "EditAgent",
    "AnimationAgent",
    "AudioAgent",
    "QualityAgent",
]
