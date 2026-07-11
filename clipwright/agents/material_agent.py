"""素材 Agent（MaterialAgent）— 素材智能匹配。

输入：脚本骨架 + 素材库
输出：候选素材集合（每个场景匹配的素材列表）
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    MaterialInput,
    MaterialOutput,
)


class MaterialAgent(BaseAgent[MaterialInput, MaterialOutput]):
    """素材 Agent：根据脚本骨架语义检索匹配素材。"""

    agent_name = "material_agent"

    async def execute(
        self, input_data: MaterialInput, context: AgentContext
    ) -> MaterialOutput:
        try:
            scenes = input_data.script_skeleton.get("scenes", [])

            # ==== Phase 1 占位实现 ====
            # 后续接入 CLIP 语义检索或素材库查询
            candidate_clips = []
            for i, scene in enumerate(scenes):
                candidate_clips.append({
                    "scene_index": i,
                    "scene_title": scene.get("title", ""),
                    "suggested_assets": [],
                    "score": 0.0,
                    "note": "占位：等待素材库接入",
                })

            return MaterialOutput(
                decision=AgentDecision.PASS,
                candidate_clips=candidate_clips,
            )

        except Exception as e:
            return self.build_error_output(str(e), MaterialOutput)
