"""音效 Agent（AudioAgent）— BGM 匹配与混音。

输入：带动画的时间线 + 音频参数
输出：混音后的时间线
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AudioInput,
    AudioOutput,
)


class AudioAgent(BaseAgent[AudioInput, AudioOutput]):
    """音效 Agent：BGM 匹配、TTS 生成、混音编排。"""

    agent_name = "audio_agent"

    async def execute(
        self, input_data: AudioInput, context: AgentContext
    ) -> AudioOutput:
        try:
            timeline = input_data.timeline

            # ==== Phase 1 占位实现 ====
            # 后续接入 BGM 匹配逻辑和 TTS 生成
            # 读取 AudioConfig 的 bgm_slots 和 voice_model

            return AudioOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
            )

        except Exception as e:
            return self.build_error_output(str(e), AudioOutput)
