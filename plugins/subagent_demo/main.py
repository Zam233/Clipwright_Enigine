"""SA-4 示例插件：注册一个自定义 Agent（子代理形态）。

演示要点：
- manifest 声明 kind=agent + permissions=[orchestrate]（缺 orchestrate 拒绝加载）
- 插件类继承 BasePlugin；initialize() 内经 AgentRegistry.register 注册
  BaseAgent 子类实例
- 注册即刻生效：主管线可将其并入执行计划（按 deps），或经
  clipwright.services.subagent.run_sub_agent 作为子代理调用
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.agents.registry import AgentRegistry
from clipwright.plugins.base import BasePlugin
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    PluginAgentInput,
    PluginAgentOutput,
)


class SummarizerAgent(BaseAgent):
    """示例子代理：把上游共享数据里的文本摘要进 payload。"""

    agent_name = "demo_summarizer"
    timeout_sec = 60

    async def execute(self, input_data: PluginAgentInput,
                      context: AgentContext) -> PluginAgentOutput:
        data = input_data.data or {}
        scenes = data.get("scenes") or []
        texts: list[str] = []
        for scene in scenes:
            if isinstance(scene, dict):
                text = str(scene.get("text") or scene.get("narration") or "")
                if text:
                    texts.append(text[:40])
        summary = " / ".join(texts[:5]) or "无可用文本"
        return PluginAgentOutput(
            agent_name=self.agent_name,
            decision=AgentDecision.PASS,
            payload={"summary": summary, "scene_count": len(scenes)},
        )


class SubagentDemoPlugin(BasePlugin):
    """插件入口：注册/注销 demo_summarizer。"""

    def initialize(self) -> None:
        AgentRegistry.register(
            SummarizerAgent(),
            name="demo_summarizer",
            plugin_id=self.manifest.id,
            deps=["edit"],
            description="示例：上游文本摘要子代理",
        )

    def shutdown(self) -> None:
        AgentRegistry.unregister_plugin(self.manifest.id)
