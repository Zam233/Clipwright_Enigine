"""素材 Agent（MaterialAgent）— 素材智能匹配。

输入：脚本骨架（场景列表）
输出：候选素材集合

工作方式：
1. 通过 MaterialRegistry 跨所有已注册的素材源搜索
2. 对每个场景，用其标题/关键词/描述做语义搜索
3. 结果排序、去重，映射为 MaterialOutput

当没有注册素材源时回退到占位输出。
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.material import MaterialRegistry
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
            all_candidates = list(MaterialRegistry._sources.values())
            has_sources = len(all_candidates) > 0

            if not has_sources:
                # 无注册素材源 → 占位输出
                candidate_clips = [
                    {
                        "scene_index": i,
                        "scene_title": scene.get("title", ""),
                        "suggested_assets": [],
                        "score": 0.0,
                        "note": "无注册素材源 — 通过 MaterialRegistry.register() 注册",
                    }
                    for i, scene in enumerate(scenes)
                ]
                return MaterialOutput(
                    decision=AgentDecision.PASS,
                    candidate_clips=candidate_clips,
                )

            # 对每个场景搜索素材
            candidate_clips = []
            for i, scene in enumerate(scenes):
                # 构造搜索 query：标题 + 关键词 + 描述
                scene_title = scene.get("title", "")
                keywords = scene.get("keywords", [])
                description = scene.get("description", "")
                query_parts = [scene_title] + keywords
                query = " ".join(q for q in query_parts if q)

                if not query:
                    query = description

                # 跨所有源搜索
                results = await MaterialRegistry.search(
                    query=query,
                    top_k_per_source=5,
                )

                suggested = [
                    {
                        "asset_id": r.asset.id,
                        "title": r.asset.title,
                        "type": r.asset.type,
                        "url": r.asset.url,
                        "local_path": r.asset.local_path,
                        "score": r.score,
                        "source": r.source_name,
                        "duration_sec": r.asset.duration_sec,
                        "tags": r.asset.tags,
                    }
                    for r in results[:5]  # 每个场景取 top-5
                ]

                max_score = max((r.score for r in results), default=0.0)
                candidate_clips.append({
                    "scene_index": i,
                    "scene_title": scene_title,
                    "suggested_assets": suggested,
                    "score": max_score,
                    "query": query,
                })

            return MaterialOutput(
                decision=AgentDecision.PASS,
                candidate_clips=candidate_clips,
            )

        except Exception as e:
            return self.build_error_output(str(e), MaterialOutput)
