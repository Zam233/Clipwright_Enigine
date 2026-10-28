"""规划书「场景改写」调用的契约（E2E 2026-10-28 发现的死分支）。

现象（K:\\demo 一镜到底，plan_ready 下提修改意见）：
- `_revise_raw_scenes` 调 `self._llm.generate(system_prompt=…, user_prompt=…)`，
  但 `LLMService.generate()` 的第一个位置参数是 `messages`——没有这个签名，
  调用立刻 TypeError，被 `except Exception` 吞成"场景改写失败（回退原场景）"。
- 结果：**修改意见永远改不动场景本体**，只剩"重译一遍规划书文案"，
  而且重译用的那次 structured_output 又没给 max_tokens（推理型模型正文为空）。
- 日志实证：`LLM 返回空内容（status=200, max_tokens=16）` +
  `规划修订：场景改写失败（回退原场景）` 同一秒出现。

本测试锁死三件事：
1. 场景改写必须走 `messages=` 形态（provider 无关）；
2. 必须带显式 `max_tokens` 预算（否则正文为空）；
3. 拿到合法 JSON 数组时，修订结果真的被采用。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services.requirements_service import RequirementsService


SCENES = [
    {
        "title": "开场",
        "description": "桌面录屏 PCL 下载页",
        "duration_sec": 12,
        "keywords": ["PCL", "下载页"],
        "voiceover_script": "今天下午我打开 PCL。",
    }
]

REVISED = [
    {
        "title": "开场（改）",
        "description": "冷色调 PCL 下载页特写",
        "duration_sec": 15,
        "keywords": ["PCL"],
        "voiceover_script": "今天下午我打开 PCL。",
    }
]


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.success = True
        self.status_code = 200


class _FakeLLM:
    """照着 LLMService.generate 的**真实签名**写：只有 messages 是位置参数。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def generate(self, messages=None, temperature=None, max_tokens=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
        if messages is None:
            raise TypeError("generate() missing required positional argument: 'messages'")
        self.calls.append({
            "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "kwargs": kwargs,
        })
        return _Resp(self.content)


def _svc(llm: _FakeLLM) -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)  # 不碰 Mongo/构造
    svc._llm = llm  # noqa: SLF001
    return svc


@pytest.mark.asyncio
async def test_revise_uses_messages_shape_and_has_budget():
    llm = _FakeLLM(json.dumps(REVISED, ensure_ascii=False))
    svc = _svc(llm)

    out = await svc._revise_raw_scenes(  # noqa: SLF001
        SCENES, "开场再冷一点、长三秒", {"title": "t"}, {"topic": "t"},
    )

    assert llm.calls, "必须真的调了一次 LLM（旧实现 TypeError 被吞，calls 为空）"
    call = llm.calls[0]
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert call["max_tokens"] and call["max_tokens"] >= 4096, (
        "必须显式给 max_tokens：不给时推理型模型正文返回空（实测 max_tokens=16）"
    )
    assert out == REVISED, "合法 JSON 数组要被采用，而不是回退 None"


@pytest.mark.asyncio
async def test_revise_returns_none_on_garbage():
    svc = _svc(_FakeLLM("这里没有任何 JSON"))
    out = await svc._revise_raw_scenes(  # noqa: SLF001
        SCENES, "改一下", {}, {},
    )
    assert out is None, "解析不出数组时回退 None（旧行为：仅重译文案）"


@pytest.mark.asyncio
async def test_revise_skips_llm_for_empty_feedback():
    llm = _FakeLLM(json.dumps(REVISED, ensure_ascii=False))
    svc = _svc(llm)
    assert await svc._revise_raw_scenes(SCENES, "   ", {}, {}) is None  # noqa: SLF001
    assert llm.calls == [], "没有修改意见时不该花一次 LLM"
