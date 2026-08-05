"""Agent 上下文总线 — Agent 间共享意图与数据。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from clipwright.config import logger


class AgentBus:
    """Agent 间共享上下文总线。

    每个 Agent 可以：
    - 发布消息到总线（其他 Agent 可以读取）
    - 读取其他 Agent 的发布内容
    - 标记"需求"（告诉其他 Agent 自己需要什么类型的素材）
    """

    def __init__(self, pipeline_id: str = ""):
        self.pipeline_id = pipeline_id or f"bus_{uuid.uuid4().hex[:8]}"
        self._messages: list[dict] = []
        self._demands: dict[str, Any] = {}  # Agent_name → 需求描述
        self._artifacts: dict[str, Any] = {}  # 共享数据

    def publish(self, agent: str, topic: str, data: Any) -> None:
        """Agent 发布信息到总线。"""
        entry = {
            "time": datetime.now().isoformat(),
            "agent": agent,
            "topic": topic,
            "data": data,
        }
        self._messages.append(entry)
        logger.debug("AgentBus [%s] %s → %s", agent, topic, str(data)[:80])

    def get_messages(self, topic: str = "", agent: str = "") -> list[dict]:
        """按主题或 Agent 筛选消息。"""
        results = self._messages
        if topic:
            results = [m for m in results if m["topic"] == topic]
        if agent:
            results = [m for m in results if m["agent"] == agent]
        return results

    def set_demand(self, agent: str, demand: dict[str, Any]) -> None:
        """Agent 声明需求（如"我需要特写镜头""我需要城市夜景素材"）。"""
        self._demands[agent] = demand

    def get_demands(self) -> dict[str, Any]:
        """获取所有 Agent 的需求。"""
        return dict(self._demands)

    def get_demand_for(self, agent: str) -> dict:
        """获取指定 Agent 的需求。"""
        return self._demands.get(agent, {})

    def set_artifact(self, key: str, value: Any) -> None:
        self._artifacts[key] = value

    def get_artifact(self, key: str, default: Any = None) -> Any:
        return self._artifacts.get(key, default)

    def route_decision(self, agent_name: str, status: str = "completed") -> str:
        """根据当前总线状态决定下一个 Agent。
        返回下一个 Agent 名称，或 "done" / "failed"。
        """
        if status == "failed":
            return "failed"

        route_map = {
            "structure": "material",
            "material": "edit",
            "edit": "animation",
            "animation": "audio",
            "audio": "quality",
            "quality": "done",
        }
        # 检查是否有 Agent 发出"重做"请求
        redo_request = self._demands.get("quality", {}).get("redo", "")
        if redo_request and redo_request in route_map:
            logger.info("AgentBus: 质检请求重做 Agent=%s", redo_request)
            return redo_request

        return route_map.get(agent_name, "done")


# 镜头意图类型
SHOT_INTENTS = {
    "main": "主镜头 — 展示主要内容/人物",
    "reaction": "反应镜头 — 对主内容的反应",
    "broll": "B-roll — 辅助说明画面",
    "transition": "过渡镜头 — 场景转换",
    "establishing": "定场镜头 — 交代环境",
    "detail": "特写 — 细节放大",
    "insert": "插入镜头 — 补充信息",
    "pip": "画中画 — 叠加小窗",
    "text": "文字/标题 — 信息文字",
    "a_roll": "A-roll — 主要讲话/解说画面",
}


class ShotIntent:
    """镜头意图 — 每个 clip 标记拍摄意图，指导剪辑决策。"""

    def __init__(
        self,
        intent_type: str = "broll",
        description: str = "",
        mood: str = "neutral",
        composition: str = "",
        duration_hint: float = 0,
    ):
        self.intent_type = intent_type
        self.description = description
        self.mood = mood
        self.composition = composition
        self.duration_hint = duration_hint

    def to_dict(self) -> dict:
        return {
            "intent_type": self.intent_type,
            "description": self.description,
            "mood": self.mood,
            "composition": self.composition,
            "duration_hint": self.duration_hint,
        }
