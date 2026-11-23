"""provider_error / 停止续跑 / 检查点契约测试。"""

from __future__ import annotations

from clipwright.services.llm import LLMProviderError
from clipwright.services.requirements_service import RequirementsService
from clipwright.services.stage_subagent import _internal_failure


def test_llm_provider_error_maps_to_provider_error() -> None:
    failure = _internal_failure(LLMProviderError(
        "LLM structured output failed (status=500)",
        status_code=500, content="Error: Connection error.",
    ))
    assert failure.category == "provider_error"
    assert failure.retryable == "retry_same"


def test_released_before_accepts_legacy_user_stopped() -> None:
    session = {"messages": [
        {"role": "assistant", "content": "已停止", "metadata": {"user_stopped": True}},
        {"role": "user", "content": "继续"},
    ]}
    assert RequirementsService._released_before(object(), session) is True


def test_released_before_scans_beyond_last_ten() -> None:
    messages = [{"role": "user", "content": str(i)} for i in range(20)]
    messages.insert(0, {
        "role": "assistant", "content": "已停止",
        "metadata": {"task_released": "cancelled"},
    })
    assert RequirementsService._released_before(object(), {"messages": messages}) is True
