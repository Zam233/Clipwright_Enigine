"""动画 Agent（AnimationAgent）— 文字动画与视觉包装。

输入：粗剪时间线 + 视觉参数
输出：带动画的时间线
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AnimationInput,
    AnimationOutput,
)


class AnimationAgent(BaseAgent[AnimationInput, AnimationOutput]):
    """动画 Agent：为时间线添加文字动画和视觉包装。"""

    agent_name = "animation_agent"

    async def execute(
        self, input_data: AnimationInput, context: AgentContext
    ) -> AnimationOutput:
        try:
            timeline = input_data.timeline

            # ==== Phase 1 占位实现 ====
            # 后续接入 Manim 或 Motion Canvas 动画生成
            # 读取 VisualConfig 获取动画风格参数

            return AnimationOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
            )

        except Exception as e:
            return self.build_error_output(str(e), AnimationOutput)
