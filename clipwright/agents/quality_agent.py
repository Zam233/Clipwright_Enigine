"""质检 Agent（QualityAgent）— 风格一致性与合规校验。

输入：完整时间线 + 规则集
输出：pass/fail + 修正建议
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    QualityInput,
    QualityIssue,
    QualityOutput,
)


class QualityAgent(BaseAgent[QualityInput, QualityOutput]):
    """质检 Agent：校验时间线是否符合 Persona 规范和硬约束。"""

    agent_name = "quality_agent"

    async def execute(
        self, input_data: QualityInput, context: AgentContext
    ) -> QualityOutput:
        try:
            issues: list[QualityIssue] = []
            constraints = input_data.constraints
            timeline = input_data.timeline

            # 时长校验
            max_duration = constraints.get("max_duration_sec", 900)
            if timeline.duration_sec > max_duration:
                issues.append(
                    QualityIssue(
                        severity="error",
                        category="duration",
                        message=f"视频时长 {timeline.duration_sec}s 超过上限 {max_duration}s",
                    )
                )

            # 轨道数量检查
            if not timeline.tracks:
                issues.append(
                    QualityIssue(
                        severity="error",
                        category="structure",
                        message="时间线没有轨道",
                    )
                )

            decision = AgentDecision.FAIL if issues else AgentDecision.PASS

            return QualityOutput(
                decision=decision,
                passed=len(issues) == 0,
                issues=issues,
                fix_suggestions=[
                    i.message for i in issues if i.severity == "error"
                ],
            )

        except Exception as e:
            return self.build_error_output(str(e), QualityOutput)
